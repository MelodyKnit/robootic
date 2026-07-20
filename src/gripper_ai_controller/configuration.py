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
