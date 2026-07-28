"""Stable exception types shared by reloadable preview plugins."""


class VisualPoseAnalysisCapabilityError(RuntimeError):
    """Report a pose operation unavailable in the configured passive plugin."""
