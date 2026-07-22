"""Configuration owned by the application rather than hardware adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyLimits:
    """Documented PGI command limits used before any physical adapter is added."""

    minimum_force_percent: int = 20
    maximum_force_percent: int = 100
    maximum_position: int = 1000
    minimum_speed_percent: int = 1
    maximum_speed_percent: int = 100
    minimum_perception_confidence: float = 0.70
    maximum_perception_age_seconds: float = 2.0


@dataclass(frozen=True)
class WebPreviewSettings:
    """Validated settings for the browser camera preview and parameter-control server.

    The values originate in a versioned JSON configuration file or explicit CLI
    overrides. Camera parameter writes remain disabled unless a local configuration
    explicitly enables the fixed adapter whitelist. These settings never enable robot
    or gripper controls, and they do not define persistence paths.
    """

    bind_host: str = "0.0.0.0"
    port: int = 8000
    frontend_dist_dir: str = "src/web/dist"
    stream_fps: int = 10
    jpeg_quality: int = 80
    capture_retry_seconds: float = 1.0
    camera_controls_enabled: bool = False
