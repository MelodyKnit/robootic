"""Safety-bounded Hikvision USB3 Vision integration built on the project-local MVS SDK."""

from gripper_ai_controller.adapters.hikvision.adapter import HikvisionAdapter, HikvisionAdapterError

__all__ = ["HikvisionAdapter", "HikvisionAdapterError"]
