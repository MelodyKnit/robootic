"""Atomic persistence for the browser-selected human-pose target joint."""

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping

from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES


class PoseTargetConfigStoreError(RuntimeError):
    """Report a pose target persistence error without exposing local filesystem details."""


class PoseTargetConfigStore:
    """Persist only the selected COCO target into the caller-selected JSON configuration.

    The store validates camera and vision identity before each write. It never derives a
    configuration location from source files or a repository root, and it deliberately
    preserves unrelated configuration fields such as camera parameters and safety limits.
    """

    def __init__(
        self,
        config_file: str,
        camera_id: str,
        vision_name: str,
        vision_adapter_settings: Mapping[str, Any],
    ) -> None:
        """Bind one explicit configuration and one preview-device identity."""

        self.config_file = Path(config_file)
        if not any(part.lower() == "localstore" for part in self.config_file.parts):
            raise PoseTargetConfigStoreError(
                "Pose target persistence requires a configuration under localstore."
            )
        self.camera_id = camera_id
        self.vision_name = vision_name
        self.vision_adapter_settings = copy.deepcopy(dict(vision_adapter_settings))

    def persist_target_joint(self, target_joint: str) -> str:
        """Atomically replace only ``pose.target_joint`` after checking the selected camera."""

        if target_joint not in COCO_KEYPOINT_NAMES:
            raise PoseTargetConfigStoreError("The configured pose target joint is not supported.")
        payload = self._load_payload()
        pose = payload.get("pose", {})
        if not isinstance(pose, dict):
            raise PoseTargetConfigStoreError("The configured pose settings must be a JSON object.")
        pose = dict(pose)
        pose["target_joint"] = target_joint
        payload["pose"] = pose
        self._write_atomically(payload)
        return target_joint

    def _load_payload(self) -> Dict[str, Any]:
        """Read and validate the target document identity before modifying pose settings."""

        if self.config_file.suffix.lower() != ".json":
            raise PoseTargetConfigStoreError("Pose target persistence requires a JSON configuration.")
        try:
            with self.config_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise PoseTargetConfigStoreError("The configured pose target file could not be read.") from error
        if not isinstance(payload, dict):
            raise PoseTargetConfigStoreError("The configured pose target file must contain a JSON object.")
        try:
            camera = payload["camera"]
            components = payload["components"]
            configured_camera_id = camera["camera_id"]
            configured_vision_name = components["vision"]
            configured_settings = components.get("vision_adapter_settings", {})
        except (KeyError, TypeError) as error:
            raise PoseTargetConfigStoreError(
                "The configured pose target file no longer describes this preview service."
            ) from error
        if configured_camera_id != self.camera_id or configured_vision_name != self.vision_name:
            raise PoseTargetConfigStoreError(
                "The configured pose target file no longer matches this preview service."
            )
        if not isinstance(configured_settings, dict) or configured_settings != self.vision_adapter_settings:
            raise PoseTargetConfigStoreError(
                "The configured pose target file no longer matches this preview device selection."
            )
        return payload

    def _write_atomically(self, payload: Mapping[str, Any]) -> None:
        """Replace the explicit configuration only after its complete JSON content is flushed."""

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
            raise PoseTargetConfigStoreError("The configured pose target file could not be saved.") from error
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
