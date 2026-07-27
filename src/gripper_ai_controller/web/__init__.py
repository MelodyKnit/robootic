"""FastAPI camera preview and manually authorized device-control web services."""

from gripper_ai_controller.web.app import create_web_app
from gripper_ai_controller.web.gripper_api import install_gripper_routes
from gripper_ai_controller.web.gripper_service import ManualGripperControlService
from gripper_ai_controller.web.jaka_api import install_jaka_routes
from gripper_ai_controller.web.jaka_service import ManualJakaControlService

__all__ = [
    "create_web_app",
    "install_gripper_routes",
    "ManualGripperControlService",
    "install_jaka_routes",
    "ManualJakaControlService",
]
