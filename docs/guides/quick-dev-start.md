# 开发环境快速启动指南

本文档说明如何使用 `scripts/run.bat --dev` 快速启动完整的开发环境。

## 🎯 一键启动

```bash
scripts\run.bat --dev
```

这个命令会：
- ✅ 自动加载 `configs/development.json` 配置
- ✅ 启动Web服务（http://127.0.0.1:8000）
- ✅ 启用所有开发功能：
  - 相机预览（模拟相机）
  - 夹爪控制（模拟夹爪）
  - 机械臂控制（模拟机械臂）
  - **相机标定**（标定面板）
  - 插件热重载
  - 插件生命周期控制

## 🔧 开发配置说明

### configs/development.json

这是开发环境的默认配置文件，包含：

#### 1. 模拟硬件
```json
{
  "components": {
    "vision": "simulated-camera"
  },
  "targets": [
    {
      "name": "sim-primary",
      "robot_adapter": "simulated-robot",
      "gripper_adapter": "simulated-gripper"
    }
  ]
}
```

- **simulated-camera**: 模拟Hikvision相机，生成测试图像
- **simulated-robot**: 模拟JAKA机械臂，不发送真实硬件命令
- **simulated-gripper**: 模拟PGI夹爪，不发送真实硬件命令

#### 2. Web控制功能
```json
{
  "jaka_control": {
    "target_name": "sim-primary"
  },
  "web": {
    "camera_controls_enabled": true,
    "gripper_controls_enabled": true,
    "jaka_controls_enabled": true,
    "plugin_reload_enabled": true,
    "plugin_lifecycle_controls_enabled": true
  }
}
```

- **camera_controls_enabled**: 相机参数调整
- **gripper_controls_enabled**: 夹爪手动控制
- **jaka_controls_enabled**: 机械臂手动控制
- **plugin_reload_enabled**: 插件热重载（开发模式）
- **plugin_lifecycle_controls_enabled**: 插件启用/禁用控制

#### 3. 预览插件
```json
{
  "components": {
    "plugins": {
      "preview": [
        "visual-pose-analysis",
        "object-pose-analysis",
        "object-detection-analysis"
      ]
    }
  }
}
```

右侧显示3个分析插件：
- **visual-pose-analysis**: 人体姿态分析
- **object-pose-analysis**: 物体姿态分析
- **object-detection-analysis**: 物体检测分析

#### 4. 标定功能

**自动启用条件**:
- ✅ 配置了 `jaka_control` 区块
- ✅ Web服务会自动构建 `AutoCalibrationPlugin`
- ✅ 标定功能出现在主界面的「相机标定」标签页

**无需额外配置**，只要有机器人适配器，标定功能就会自动启用。

---

## 🚀 访问功能

启动后访问 `http://127.0.0.1:8000`，你会看到：

### 左侧：相机预览
- 实时模拟相机画面
- 覆盖层显示检测结果

### 中间：硬件适配器
4个标签页：
1. **相机适配器** - 参数调整
2. **夹爪适配器** - 夹爪控制
3. **机械臂适配器** - 机械臂控制
4. **相机标定** ← 标定面板

### 右侧：分析插件
3个预览插件：
1. 人体姿态分析
2. 物体姿态分析
3. 物体检测分析

---

## 📝 无需手动配置

与之前相比，现在：

**之前**:
```bash
# 需要指定配置文件
poetry run python -m gripper_ai_controller web --config-file configs/some-config.json

# 或者需要记住复杂参数
scripts\run.bat web --config-file configs/development.json --host 127.0.0.1 --port 8000
```

**现在**:
```bash
# 一条命令搞定
scripts\run.bat --dev
```

所有配置都在 `configs/development.json` 中预设好了，开发时直接启动即可。

---

## 🔄 切换真实硬件

如果要连接真实硬件，创建 `localstore/hikvision-web.local.json`:

```json
{
  "runtime_mode": "development",
  "camera": {
    "camera_id": "hikvision-usb",
    "calibration_id": "hikvision-intrinsic"
  },
  "components": {
    "vision": "hikvision-usb"
  },
  "targets": [
    {
      "name": "jaka-real",
      "role": "primary",
      "robot_adapter": "jaka-robot",
      "robot_adapter_settings": {
        "host": "192.168.1.100",
        "port": 10000
      },
      "gripper_adapter": "pgi-tcp",
      "gripper_adapter_settings": {
        "host": "192.168.1.101",
        "port": 2000
      }
    }
  ],
  "jaka_control": {
    "target_name": "jaka-real"
  },
  "web": {
    "bind_host": "127.0.0.1",
    "port": 8000,
    "frontend_dist_dir": "src/web/dist",
    "camera_controls_enabled": true,
    "gripper_controls_enabled": true,
    "jaka_controls_enabled": true
  }
}
```

`run.bat --dev` 会优先使用 `localstore/hikvision-web.local.json`（如果存在）。

---

## ⚡ 其他启动方式

### 普通模式（非开发）
```bash
scripts\run.bat
```
- 使用 `development.json` 或 `localstore/hikvision-web.local.json`
- 不启用热重载

### 指定配置文件
```bash
scripts\run.bat web --config-file configs/your-config.json
```

### 其他命令
```bash
# 运行自主任务
scripts\run.bat run --config-file configs/production.json --objective pick-and-place

# 读取机械臂关节位置
scripts\run.bat jaka-joints --config-file configs/jaka-hardware.json

# GPU检查
scripts\run.bat gpu-check --require-torch
```

---

## 📚 配置文件位置

- **开发默认**: `configs/development.json`
- **本地覆盖**: `localstore/hikvision-web.local.json`（优先级更高）
- **其他示例**: `configs/*.example.json`

---

## ✅ 总结

开发时记住一条命令：

```bash
scripts\run.bat --dev
```

就能启动包含标定功能在内的完整开发环境！🎉

无需记忆复杂的参数，无需手动指定配置文件，一键启动，开箱即用。
