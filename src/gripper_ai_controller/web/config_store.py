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
