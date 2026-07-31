#!/usr/bin/env python3
"""测试配置引用解析功能"""

import sys
from pathlib import Path

# 添加src目录到Python路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from gripper_ai_controller.bootstrap.runtime_builder import load_json_config


def test_reference_loading():
    """测试@引用加载功能"""

    print("=" * 60)
    print("测试1: 加载使用引用的配置文件")
    print("=" * 60)

    try:
        config = load_json_config("configs/development-with-refs.json")
        print("✓ 配置加载成功")

        # 验证vision适配器引用被正确解析
        vision = config["components"]["vision"]
        print(f"✓ vision适配器: {vision}")
        assert vision == "simulated-camera", f"期望 'simulated-camera', 实际 '{vision}'"

        # 验证插件引用被正确解析
        perception = config["components"]["plugins"]["perception"]
        print(f"✓ perception插件: {perception}")
        assert perception == "deterministic-perception", f"期望 'deterministic-perception', 实际 '{perception}'"

        # 验证列表中的引用
        planners = config["components"]["plugins"]["planners"]
        print(f"✓ planner插件: {planners}")
        assert planners == ["demonstration-planner"], f"期望 ['demonstration-planner'], 实际 {planners}"

        # 验证target中的引用
        robot_adapter = config["targets"][0]["robot_adapter"]
        print(f"✓ robot适配器: {robot_adapter}")
        assert robot_adapter == "simulated-robot", f"期望 'simulated-robot', 实际 '{robot_adapter}'"

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def test_backward_compatibility():
    """测试向后兼容性：原有配置仍然有效"""

    print("\n" + "=" * 60)
    print("测试2: 向后兼容性 - 加载原有配置文件")
    print("=" * 60)

    try:
        config = load_json_config("configs/development.json")
        print("✓ 原有配置加载成功")

        vision = config["components"]["vision"]
        print(f"✓ vision适配器: {vision}")
        assert vision == "simulated-camera"

        print("\n✓ 向后兼容性测试通过！")

    except Exception as e:
        print(f"\n✗ 向后兼容性测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def test_missing_reference():
    """测试引用文件不存在的错误处理"""

    print("\n" + "=" * 60)
    print("测试3: 错误处理 - 引用文件不存在")
    print("=" * 60)

    # 创建一个包含无效引用的临时配置
    test_config_path = Path("configs/test-invalid-ref.json")
    test_config_path.write_text('''{
  "runtime_mode": "development",
  "components": {
    "vision": "@adapters/nonexistent.json"
  }
}''')

    try:
        config = load_json_config(str(test_config_path))
        print("✗ 应该抛出FileNotFoundError，但没有")
        test_config_path.unlink()
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"✓ 正确抛出FileNotFoundError: {e}")
        test_config_path.unlink()
    except Exception as e:
        print(f"✗ 抛出了错误的异常类型: {e}")
        test_config_path.unlink()
        sys.exit(1)


if __name__ == "__main__":
    test_reference_loading()
    test_backward_compatibility()
    test_missing_reference()

    print("\n" + "=" * 60)
    print("✓✓✓ 所有测试通过！配置引用功能正常工作 ✓✓✓")
    print("=" * 60)
