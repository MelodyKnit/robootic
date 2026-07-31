"""设置物体姿态分析配置"""
import json
from pathlib import Path

def main():
    project_root = Path(__file__).parent.parent

    # 创建配置目录
    config_dir = project_root / "localstore" / "object_pose_profiles"
    config_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("物体姿态分析配置工具")
    print("=" * 60)
    print()

    # 通用工具配置
    tool_profile = {
        "name": "generic_tool",
        "description": "通用工具物体（扳手、钳子等）",
        "shape_constraints": {
            "aspect_ratio": {"min": 2.0, "max": 8.0},
            "min_area": 1000,
            "max_area": 50000,
            "convexity": {"min": 0.7}
        },
        "grasp_points": {
            "strategy": "along_major_axis",
            "offset_ratio": 0.4,
            "approach_angle": "perpendicular"
        }
    }

    # 瓶子/杯子配置
    container_profile = {
        "name": "container",
        "description": "瓶子、杯子等容器",
        "shape_constraints": {
            "aspect_ratio": {"min": 1.0, "max": 4.0},
            "min_area": 2000,
            "max_area": 100000,
            "circularity": {"min": 0.6}
        },
        "grasp_points": {
            "strategy": "vertical_center",
            "offset_ratio": 0.5,
            "approach_angle": "vertical"
        }
    }

    # 扳手配置
    wrench_profile = {
        "name": "wrench",
        "description": "扳手（细长工具）",
        "shape_constraints": {
            "aspect_ratio": {"min": 3.0, "max": 10.0},
            "min_area": 1500,
            "max_area": 30000,
            "elongation": {"min": 0.7}
        },
        "grasp_points": {
            "strategy": "handle_center",
            "offset_ratio": 0.3,
            "approach_angle": "perpendicular"
        }
    }

    # 保存配置
    profiles = [tool_profile, container_profile, wrench_profile]

    for profile in profiles:
        output_path = config_dir / f"{profile['name']}.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        print(f"✓ {profile['description']}: {output_path.name}")

    # 创建主配置文件
    main_config = {
        "enabled": True,
        "profiles_dir": "localstore/object_pose_profiles",
        "default_profile": "generic_tool",
        "analysis": {
            "contour_method": "cv2.CHAIN_APPROX_SIMPLE",
            "min_contour_area": 500,
            "hierarchy_level": 0
        },
        "visualization": {
            "draw_contours": True,
            "draw_bounding_box": True,
            "draw_orientation": True,
            "draw_grasp_points": True
        }
    }

    main_config_path = project_root / "localstore" / "object_pose_config.json"
    with open(main_config_path, 'w', encoding='utf-8') as f:
        json.dump(main_config, f, indent=2, ensure_ascii=False)

    print()
    print(f"✓ 主配置: {main_config_path.relative_to(project_root)}")
    print()
    print("=" * 60)
    print("配置完成！")
    print("=" * 60)
    print()
    print("物体配置文件说明:")
    print("  - generic_tool.json : 通用工具（扳手、钳子）")
    print("  - container.json    : 容器（瓶子、杯子）")
    print("  - wrench.json       : 扳手专用")
    print()
    print("使用方法:")
    print("  在检测配置中启用 object_pose 分析即可自动加载")
    print()
    print("自定义配置:")
    print(f"  编辑 {config_dir.relative_to(project_root)} 中的 JSON 文件")

if __name__ == "__main__":
    main()
