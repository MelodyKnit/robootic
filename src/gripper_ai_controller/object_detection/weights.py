"""Explicitly install generic detection model weights without runtime downloads."""

import hashlib
import os
from pathlib import Path
import tempfile
from typing import BinaryIO, Callable
from urllib.request import urlopen

from gripper_ai_controller.object_detection.models import DetectionProviderError


FASTER_RCNN_COCO_WEIGHTS_URL = (
    "https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_coco-258fb6c6.pth"
)
FASTER_RCNN_COCO_SHA256 = (
    "258fb6c638b15964ddcdd1ae0748c5eef1be9e732750120cc857feed3faac384"
)


def install_faster_rcnn_coco_weights(
    destination: str,
    opener: Callable[..., BinaryIO] = urlopen,
) -> str:
    """Download verified official weights only to a localstore-relative target.

    The public boundary validates the path itself so callers other than the CLI
    cannot redirect model downloads into versioned source, arbitrary local
    directories, or an absolute filesystem location.
    """

    destination_path = _resolve_localstore_destination(destination)
    temporary_path = None
    try:
        if destination_path.is_file() and _sha256(destination_path) == FASTER_RCNN_COCO_SHA256:
            return str(Path(destination))
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="{0}.".format(destination_path.name),
            suffix=".tmp",
            dir=str(destination_path.parent),
        )
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as handle:
            with opener(FASTER_RCNN_COCO_WEIGHTS_URL, timeout=30) as response:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        if digest.hexdigest() != FASTER_RCNN_COCO_SHA256:
            raise DetectionProviderError(
                "The downloaded Faster R-CNN weights failed SHA-256 verification."
            )
        os.replace(temporary_path, destination_path)
        temporary_path = None
    except DetectionProviderError:
        raise
    except OSError as error:
        raise DetectionProviderError(
            "The local Faster R-CNN weights file could not be written."
        ) from error
    except Exception as error:
        raise DetectionProviderError(
            "The official Faster R-CNN weights could not be downloaded."
        ) from error
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    return str(Path(destination))


def _resolve_localstore_destination(destination: str) -> Path:
    """Resolve one safe destination below the project-local localstore directory."""

    if not isinstance(destination, str) or not destination.strip():
        raise DetectionProviderError(
            "The Faster R-CNN weights destination must be a non-empty localstore-relative path."
        )
    requested_path = Path(destination)
    if (
        requested_path.is_absolute()
        or requested_path.drive
        or requested_path.root
        or ".." in requested_path.parts
        or len(requested_path.parts) < 2
        or requested_path.parts[0].lower() != "localstore"
    ):
        raise DetectionProviderError(
            "The Faster R-CNN weights destination must be a localstore-relative path without parent traversal."
        )
    try:
        localstore_root = _localstore_root()
        destination_path = (localstore_root / Path(*requested_path.parts[1:])).resolve()
        destination_path.relative_to(localstore_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise DetectionProviderError(
            "The Faster R-CNN weights destination resolves outside localstore."
        ) from error
    return destination_path


def _localstore_root() -> Path:
    """Return the configured project-local storage root used by existing CLI paths."""

    return Path("localstore").resolve()


def _sha256(path: Path) -> str:
    """Calculate a local weights hash in fixed-size blocks without loading it at once."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)
