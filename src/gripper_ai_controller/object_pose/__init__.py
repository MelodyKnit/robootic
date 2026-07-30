"""Passive two-dimensional foreground analysis for a known workpiece under a fixed camera."""

from gripper_ai_controller.object_pose.estimator import ForegroundObjectPoseEstimator
from gripper_ai_controller.object_pose.models import (
    KnownWorkpieceProfile,
    NormalizedPoint2D,
    NormalizedRect,
    OBJECT_POSE_REASON_CODES,
    ObjectPoseAnalysis,
    ObjectPoseCandidate,
    ObjectPoseEstimator,
    ObjectPoseSettings,
    PixelPoint2D,
)
from gripper_ai_controller.object_pose.tracker import (
    ObjectPoseTrackingService,
    ObjectPoseTrackingSnapshot,
    OrientationRpy,
    WorkpiecePose,
)

__all__ = (
    "ForegroundObjectPoseEstimator",
    "KnownWorkpieceProfile",
    "NormalizedPoint2D",
    "NormalizedRect",
    "OBJECT_POSE_REASON_CODES",
    "ObjectPoseAnalysis",
    "ObjectPoseCandidate",
    "ObjectPoseEstimator",
    "ObjectPoseSettings",
    "PixelPoint2D",
    "ObjectPoseTrackingService",
    "ObjectPoseTrackingSnapshot",
    "OrientationRpy",
    "WorkpiecePose",
)
