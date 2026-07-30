"""Atomic persistence for browser-applied camera settings in explicit JSON configurations."""

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from gripper_ai_controller.configuration import (
    CameraParameterConfigurationError,
    validate_camera_parameter_overrides,
)
from gripper_ai_controller.domain.models import CameraParameterValue


class CameraParameterConfigStoreError(RuntimeError):
    """Report a configuration read or write failure without leaking filesystem details."""


class CameraSelectionConfigStoreError(RuntimeError):
    """Report a rejected local camera-selection persistence operation."""


class PluginLifecycleConfigStoreError(RuntimeError):
    """Report a rejected local preview-plugin lifecycle persistence operation."""


class CameraParameterConfigStore:
    """Persist normalized settings only to the JSON file selected by the CLI caller.

    The store has no repository-root discovery or default path. It validates that the
    source still names the expected camera, vision adapter, and adapter selection
    settings before every atomic write, so an external edit cannot silently redirect
    one running preview service to another device configuration.
    """

    def __init__(
        self,
        config_file: str,
        camera_id: str,
        vision_name: str,
        vision_adapter_settings: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Bind one explicit JSON document and the preview graph it originally described."""

        self.config_file = Path(config_file)
        self.camera_id = camera_id
        self.vision_name = vision_name
        if vision_adapter_settings is None:
            vision_adapter_settings = {}
        if not isinstance(vision_adapter_settings, Mapping):
            raise ValueError("vision_adapter_settings must be a mapping.")
        self.vision_adapter_settings = copy.deepcopy(dict(vision_adapter_settings))

    def persist_values(
        self, observed_values: Mapping[str, CameraParameterValue]
    ) -> Dict[str, CameraParameterValue]:
        """Merge observed device values into ``camera_parameters`` using atomic replacement.

        This method is synchronous because the caller invokes it in an executor while
        holding the preview operation lock. The device is already updated before this
        method runs; a failure therefore means the current session changed but a future
        process restart cannot yet restore the requested settings.
        """

        values = self._validate_values(observed_values)
        if not values:
            return self.load_values()
        payload = self._load_payload()
        current_values = self._validate_values(payload.get("camera_parameters", {}))
        current_values.update(values)
        payload["camera_parameters"] = current_values
        self._write_atomically(payload)
        return dict(current_values)

    def replace_values(
        self, configured_values: Mapping[str, CameraParameterValue]
    ) -> Dict[str, CameraParameterValue]:
        """Replace the complete managed override mapping using atomic file replacement.

        A complete replacement is used after a browser update because changing an
        automatic control can intentionally remove a now-invalid manual override.
        The rest of the JSON document remains untouched.
        """

        values = self._validate_values(configured_values)
        payload = self._load_payload()
        payload["camera_parameters"] = values
        self._write_atomically(payload)
        return dict(values)

    def load_values(self) -> Dict[str, CameraParameterValue]:
        """Read the current normalized override mapping from the explicit source file."""

        payload = self._load_payload()
        return self._validate_values(payload.get("camera_parameters", {}))

    @staticmethod
    def _validate_values(value: Any) -> Dict[str, CameraParameterValue]:
        """Convert JSON-shape validation failures into storage-boundary errors."""

        try:
            return validate_camera_parameter_overrides(value)
        except CameraParameterConfigurationError as error:
            raise CameraParameterConfigStoreError(
                "The configured camera parameter values are invalid."
            ) from error

    def _load_payload(self) -> Dict[str, Any]:
        """Read and validate the target document identity before modifying its settings."""

        if self.config_file.suffix.lower() != ".json":
            raise CameraParameterConfigStoreError("Camera parameter persistence requires a JSON configuration.")
        try:
            with self.config_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise CameraParameterConfigStoreError(
                "The configured camera parameter file could not be read."
            ) from error
        if not isinstance(payload, dict):
            raise CameraParameterConfigStoreError("The configured camera parameter file must contain a JSON object.")
        try:
            camera = payload["camera"]
            components = payload["components"]
            configured_camera_id = camera["camera_id"]
            configured_vision_name = components["vision"]
            configured_vision_adapter_settings = components.get("vision_adapter_settings", {})
        except (KeyError, TypeError) as error:
            raise CameraParameterConfigStoreError(
                "The configured camera parameter file no longer describes this preview service."
            ) from error
        if configured_camera_id != self.camera_id or configured_vision_name != self.vision_name:
            raise CameraParameterConfigStoreError(
                "The configured camera parameter file no longer matches this preview service."
            )
        if (
            not isinstance(configured_vision_adapter_settings, dict)
            or configured_vision_adapter_settings != self.vision_adapter_settings
        ):
            raise CameraParameterConfigStoreError(
                "The configured camera parameter file no longer matches this preview device selection."
            )
        return payload

    def _write_atomically(self, payload: Mapping[str, Any]) -> None:
        """Replace the selected JSON file only after a complete temporary document is flushed."""

        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix="{0}.".format(self.config_file.name),
                suffix=".tmp",
                dir=str(self.config_file.parent),
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.config_file)
            temporary_path = None
        except OSError as error:
            raise CameraParameterConfigStoreError(
                "The configured camera parameter file could not be saved."
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


class CameraSelectionConfigStore(CameraParameterConfigStore):
    """Persist one opaque device selection in an explicit local configuration.

    The selected value is stored in the dedicated ``camera_selection`` section,
    leaving vendor adapter settings unchanged. This preserves the identity checks
    used by camera-parameter and pose-target stores while keeping a physical serial
    number out of the browser contract and versioned configuration templates.
    """

    def __init__(
        self,
        config_file: str,
        camera_id: str,
        vision_name: str,
        vision_adapter_settings: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Bind selection writes to one ignored ``localstore`` JSON document."""

        super().__init__(config_file, camera_id, vision_name, vision_adapter_settings)
        try:
            resolved_config_file = self.config_file.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise CameraSelectionConfigStoreError(
                "Camera selection persistence requires an existing local configuration."
            ) from error
        if not any(
            part.lower() == "localstore"
            for part in resolved_config_file.parent.parts
        ):
            raise CameraSelectionConfigStoreError(
                "Camera selection persistence requires a configuration under localstore."
            )
        # Persist through the canonical target so ``..`` segments or a symlink cannot
        # redirect a later atomic replacement into version-controlled configuration.
        self.config_file = resolved_config_file

    def persist_selected_device_id(self, device_id: str) -> str:
        """Atomically replace only the selected opaque device identifier."""

        if not isinstance(device_id, str) or not device_id.strip():
            raise CameraSelectionConfigStoreError(
                "The selected camera device identifier must be a non-empty string."
            )
        try:
            payload = self._load_payload()
            selection = payload.get("camera_selection")
            if not isinstance(selection, dict) or selection.get("enabled") is not True:
                raise CameraSelectionConfigStoreError(
                    "Camera selection is not enabled by the local configuration."
                )
            selection = dict(selection)
            selection["selected_device_id"] = device_id
            payload["camera_selection"] = selection
            self._write_atomically(payload)
        except CameraSelectionConfigStoreError:
            raise
        except (CameraParameterConfigStoreError, OSError, ValueError) as error:
            raise CameraSelectionConfigStoreError(
                "The selected camera could not be saved to the local configuration."
            ) from error
        return device_id


class PluginLifecycleConfigStore(CameraParameterConfigStore):
    """Persist enabled preview plugins only to the explicit local configuration file.

    ``components.plugins.preview`` stays a versioned, trusted availability list. This
    store owns the mutable ``plugin_runtime.enabled`` map, so a browser refresh or a
    later service restart uses the operator's last deliberate enable/disable choice
    without allowing the browser to add module identifiers or import locations.
    """

    def __init__(
        self,
        config_file: str,
        camera_id: str,
        vision_name: str,
        preview_plugin_ids,
        vision_adapter_settings: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Bind plugin state writes to one verified local JSON configuration document."""

        super().__init__(config_file, camera_id, vision_name, vision_adapter_settings)
        if not isinstance(preview_plugin_ids, (tuple, list)) or not preview_plugin_ids:
            raise ValueError("preview_plugin_ids must be a non-empty sequence.")
        if any(not isinstance(plugin_id, str) or not plugin_id for plugin_id in preview_plugin_ids):
            raise ValueError("preview_plugin_ids must contain non-empty strings.")
        if len(set(preview_plugin_ids)) != len(preview_plugin_ids):
            raise ValueError("preview_plugin_ids must not contain duplicates.")
        self.preview_plugin_ids = tuple(preview_plugin_ids)
        try:
            resolved_config_file = self.config_file.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise PluginLifecycleConfigStoreError(
                "Plugin lifecycle persistence requires an existing local configuration."
            ) from error
        if not any(part.lower() == "localstore" for part in resolved_config_file.parent.parts):
            raise PluginLifecycleConfigStoreError(
                "Plugin lifecycle persistence requires a configuration under localstore."
            )
        self.config_file = resolved_config_file

    def persist_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, bool]:
        """Atomically write the complete trusted enabled map after one valid selection."""

        if plugin_id not in self.preview_plugin_ids:
            raise PluginLifecycleConfigStoreError(
                "The requested plugin is not configured for this preview."
            )
        if type(enabled) is not bool:
            raise PluginLifecycleConfigStoreError("Plugin enabled state must be a boolean.")
        try:
            payload = self._load_payload()
            self._validate_preview_plugin_ids(payload)
            enabled_states = self._read_enabled_states(payload)
            enabled_states[plugin_id] = enabled
            payload["plugin_runtime"] = {"enabled": enabled_states}
            self._write_atomically(payload)
            return dict(enabled_states)
        except PluginLifecycleConfigStoreError:
            raise
        except (CameraParameterConfigStoreError, OSError, ValueError) as error:
            raise PluginLifecycleConfigStoreError(
                "The selected plugin state could not be saved to the local configuration."
            ) from error

    def _validate_preview_plugin_ids(self, payload: Mapping[str, Any]) -> None:
        """Reject a file externally changed to describe a different preview module set."""

        try:
            components = payload["components"]
            plugins = components.get("plugins", {})
            configured = plugins.get("preview")
        except (AttributeError, KeyError, TypeError) as error:
            raise PluginLifecycleConfigStoreError(
                "The configured plugin lifecycle file no longer describes this preview."
            ) from error
        if not isinstance(configured, list) or tuple(configured) != self.preview_plugin_ids:
            raise PluginLifecycleConfigStoreError(
                "The configured plugin lifecycle file no longer matches this preview plugin list."
            )

    def _read_enabled_states(self, payload: Mapping[str, Any]) -> Dict[str, bool]:
        """Normalize a partial persisted map while retaining legacy all-enabled behavior."""

        runtime = payload.get("plugin_runtime", {})
        if not isinstance(runtime, Mapping) or set(runtime).difference({"enabled"}):
            raise PluginLifecycleConfigStoreError("plugin_runtime must contain only enabled.")
        configured = runtime.get("enabled", {})
        if not isinstance(configured, Mapping):
            raise PluginLifecycleConfigStoreError("plugin_runtime.enabled must be a JSON object.")
        unknown = set(configured).difference(self.preview_plugin_ids)
        if unknown:
            raise PluginLifecycleConfigStoreError(
                "plugin_runtime.enabled contains an unconfigured plugin."
            )
        result = {}  # type: Dict[str, bool]
        for configured_plugin_id in self.preview_plugin_ids:
            value = configured.get(configured_plugin_id, True)
            if type(value) is not bool:
                raise PluginLifecycleConfigStoreError(
                    "plugin_runtime.enabled values must be booleans."
                )
            result[configured_plugin_id] = value
        return result
