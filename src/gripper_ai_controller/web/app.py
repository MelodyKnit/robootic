"""FastAPI application factory for the read-only browser camera preview."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig
from gripper_ai_controller.web.models import CameraPreviewStatus
from gripper_ai_controller.web.service import CameraPreviewService


class ApiErrorResponse(BaseModel):
    """Stable JSON error payload returned by the read-only preview API."""

    code: str
    message: str


class CameraErrorResponse(BaseModel):
    """A camera preview fault safe for presentation to an unauthenticated browser."""

    code: str
    message: str


class CameraStatusResponse(BaseModel):
    """Public state of one configured camera preview pipeline."""

    camera_id: str
    state: str
    latest_frame_at: Optional[float]
    frame_sequence: Optional[int]
    error: Optional[CameraErrorResponse]


class CameraListResponse(BaseModel):
    """A resource-oriented list response retained for future multi-camera expansion."""

    cameras: List[CameraStatusResponse]


def create_web_app(
    preview_config: VisionPreviewConfig,
    frontend_dist_dir: Optional[str] = None,
    preview_service: Optional[CameraPreviewService] = None,
) -> FastAPI:
    """Create one FastAPI app that owns only the configured vision preview service."""

    service = preview_service or CameraPreviewService(
        preview_config.camera_id, preview_config.vision, preview_config.settings
    )

    @asynccontextmanager
    async def lifespan(application):
        """Start and stop only camera preview resources with the ASGI application."""

        await service.startup()
        try:
            yield
        finally:
            await service.shutdown()

    application = FastAPI(
        title="Gripper AI Camera Preview",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.camera_preview_service = service

    @application.get("/api/cameras", response_model=CameraListResponse)
    async def list_cameras() -> CameraListResponse:
        """Return the configured camera list without starting any additional capture task."""

        return {"cameras": [_status_response(await service.hub.status())]}

    @application.get("/api/cameras/{camera_id}/status", response_model=CameraStatusResponse)
    async def camera_status(camera_id: str):
        """Return the current capture and retry state for one known camera resource."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return _status_response(await service.hub.status())

    @application.get("/api/cameras/{camera_id}/frame")
    async def camera_frame(camera_id: str):
        """Return the latest JPEG snapshot or a normalized unavailable-camera error."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        frame = await service.hub.latest_frame()
        if frame is None:
            return _error_response(
                503,
                "camera_unavailable",
                "No browser-ready camera frame is available yet.",
            )
        return Response(
            content=frame.jpeg_payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Frame-Sequence": str(frame.sequence),
            },
        )

    @application.get("/api/cameras/{camera_id}/stream")
    async def camera_stream(camera_id: str):
        """Return one MJPEG stream that reuses the shared latest-frame acquisition loop."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown

        async def generate_mjpeg():
            """Yield each newest JPEG once per client without allocating a second camera loop."""

            sequence = 0
            while True:
                frame = await service.hub.wait_for_frame(sequence)
                if frame is None:
                    return
                sequence = frame.sequence
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + "Content-Length: {0}\r\n\r\n".format(len(frame.jpeg_payload)).encode("ascii")
                    + frame.jpeg_payload
                    + b"\r\n"
                )

        return StreamingResponse(
            generate_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    _mount_frontend_if_present(application, frontend_dist_dir)
    return application


def _status_response(status: CameraPreviewStatus):
    """Map an internal immutable status object to the documented API response shape."""

    error = None
    if status.error is not None:
        error = {"code": status.error.code, "message": status.error.message}
    return {
        "camera_id": status.camera_id,
        "state": status.state,
        "latest_frame_at": status.latest_frame_at,
        "frame_sequence": status.frame_sequence,
        "error": error,
    }


def _unknown_camera_response(requested_camera_id: str, configured_camera_id: str):
    """Return a stable 404 payload when a request names an unconfigured camera resource."""

    if requested_camera_id == configured_camera_id:
        return None
    return _error_response(404, "camera_not_found", "The requested camera is not configured for preview.")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return a normalized JSON error rather than FastAPI's framework-specific detail shape."""

    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def _mount_frontend_if_present(application: FastAPI, frontend_dist_dir: Optional[str]) -> None:
    """Mount Vite build output only when the caller supplies an existing explicit path."""

    if frontend_dist_dir is None:
        return
    path = Path(frontend_dist_dir)
    if not path.is_dir():
        return
    application.mount("/", StaticFiles(directory=str(path), html=True), name="frontend")
