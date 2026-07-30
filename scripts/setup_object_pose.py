"""物品姿态识别配置向导"""

import json
import shutil
from pathlib import Path
from datetime import datetime


def setup_object_pose_directories(project_root: Path, camera_id: str) -> dict:
    """创建必要的目录结构"""

    pose_dir = project_root / "localstore" / "object-pose" / camera_id
    pose_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "pose_dir": pose_dir,
        "background_image": pose_dir / "empty-table.png",
        "calibration_file": pose_dir / "workcell-calibration.json",
        "profiles_dir": pose_dir / "profiles"
    }

    paths["profiles_dir"].mkdir(exist_ok=True)

    return paths


def create_sample_calibration(calibration_path: Path, calibration_id: str):
    """创建示例标定文件（需要用户后续真实标定替换）"""

    sample_calibration = {
        "calibration_id": calibration_id,
        "camera_id": "hikvision-usb",
        "created_at": datetime.now().isoformat(),
        "calibration_type": "plane",
        "note": "这是示例标定文件，需要运行真实标定流程替换",
        "camera_matrix": {
            "fx": 1000.0,
            "fy": 1000.0,
            "cx": 960.0,
            "cy": 540.0
        },
        "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
        "plane_to_base_transform": {
            "rotation_matrix": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0]
            ],
            "translation_mm": [0.0, 0.0, 500.0]
        },
        "pixels_per_mm": 2.5
    }

    with open(calibration_path, "w", encoding="utf-8") as f:
        json.dump(sample_calibration, f, ensure_ascii=False, indent=2)


def create_generic_tool_profile() -> dict:
    """创建通用工具的宽松配置"""

    return {
        "profile_id": "generic-tool",
        "display_name": "通用工具",
        "minimum_area_px": 400,
        "maximum_area_px": 10000,
        "minimum_aspect_ratio": 1.0,
        "maximum_aspect_ratio": None,
        "minimum_fill_ratio": 0.2,
        "maximum_fill_ratio": 1.0,
        "minimum_solidity": 0.3,
        "require_directional_yaw": False,
        "directional_feature": "none",
        "minimum_directional_asymmetry": 0.0,
        "nominal_length_mm": None,
        "nominal_width_mm": None,
        "object_thickness_mm": None,
        "grasp_origin_offset_x_mm": 0.0,
        "grasp_origin_offset_y_mm": 0.0,
        "maximum_planar_dimension_error_ratio": 0.3,
        "note": "宽松的通用配置，适合初次测试"
    }


def update_config_with_object_pose(config_path: Path, camera_id: str, calibration_id: str, profile: dict):
    """更新配置文件，启用 object-pose"""

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    object_pose_config = {
        "enabled": True,
        "background_reference_path": f"localstore/object-pose/{camera_id}/empty-table.png",
        "workcell_calibration_path": f"localstore/object-pose/{camera_id}/workcell-calibration.json",
        "expected_calibration_id": calibration_id,
        "max_analysis_fps": 2,
        "overlay_max_frame_lag_seconds": 0.35,
        "stable_required_frames": 3,
        "maximum_center_jitter_px": 4.0,
        "maximum_yaw_jitter_rad": 0.0523598776,
        "grasp_height_mm": 0.0,
        "maximum_reprojection_error_px": 0.5,
        "maximum_base_fit_rms_error_mm": 1.0,
        "profile": profile,
        "difference_threshold": 25,
        "roi": None,
        "excluded_regions": [],
        "morphology_kernel_size": 3,
        "opening_iterations": 1,
        "closing_iterations": 1,
        "maximum_foreground_ratio": 0.45,
        "minimum_orientation_eccentricity": 0.15,
        "maximum_contour_points": 96
    }

    config["object_pose"] = object_pose_config

    if "components" not in config:
        config["components"] = {}
    if "plugins" not in config["components"]:
        config["components"]["plugins"] = {}
    if "preview" not in config["components"]["plugins"]:
        config["components"]["plugins"]["preview"] = []

    preview_plugins = config["components"]["plugins"]["preview"]
    if "object-pose-analysis" not in preview_plugins:
        preview_plugins.append("object-pose-analysis")

    if "web" in config:
        config["web"]["plugin_lifecycle_controls_enabled"] = True

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def main():
    project_root = Path(__file__).parent.parent
    config_file = project_root / "localstore" / "hikvision-object-detection.local.json"

    if not config_file.exists():
        print(f"配置文件不存在: {config_file}")
        return

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    camera_id = config.get("camera", {}).get("camera_id", "hikvision-usb")
    calibration_id = config.get("camera", {}).get("calibration_id", "local-preview-unverified")

    paths = setup_object_pose_directories(project_root, camera_id)
    print(f"✓ 创建目录: {paths['pose_dir']}")

    if not paths["calibration_file"].exists():
        create_sample_calibration(paths["calibration_file"], calibration_id)
        print(f"✓ 创建示例标定文件")

    profile = create_generic_tool_profile()
    profile_file = paths["profiles_dir"] / f"{profile['profile_id']}.json"
    with open(profile_file, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    print(f"✓ 保存配置: {profile_file}")

    update_config_with_object_pose(config_file, camera_id, calibration_id, profile)
    print("✓ 配置已更新")
    print()
    print("下一步: 拍摄空背景图并保存到:")
    print(f"  {paths['background_image']}")


if __name__ == "__main__":
    main()
