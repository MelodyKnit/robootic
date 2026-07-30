# 单目已知工件标定流程

本流程为固定安装的单台 RGB/Mono 工业相机建立被动的平面工件测量依据。除明确执行的 `calibration-capture-charuco` 外，标定命令只处理本机文件与操作者通过示教器记录的坐标。采集命令只会连接配置中的相机以读取帧；所有命令都不会连接 JAKA 或夹爪，也不会发送任何运动、上电或夹紧指令。

首期使用的 ChArUco 板固定为：`DICT_5X5_100`、`7 x 5` 方格、方格边长 `25 mm`、标记边长 `18 mm`。外形尺寸为 `175 mm x 125 mm`。打印时必须关闭“适合页面”或任何缩放，实际量取方格边长应为 `25 mm`。

## 文件边界

真实采集图、空桌背景、相机内参、示教点和完整工作单元标定只能放在 `localstore/`。该目录内容被 Git 忽略。可打印的板图可放在 `temp/gripper-ai-controller/`，或保存在 `localstore/` 供本机复用。

所有 Windows 命令均从项目根目录通过 `scripts\calibration.bat` 调用。命令会拒绝绝对路径、`..` 路径穿越和不在允许目录中的输入输出路径。

## 1. 生成并检查标定板

```powershell
scripts\calibration.bat calibration-generate-charuco --output-file temp/gripper-ai-controller/charuco/board.png
```

该命令生成 PNG，不连接任何设备。打印后量取多个方格；任一方格不是 `25 mm` 时必须重新打印。不要自行改变字典、方格数量或标记尺寸，否则此前的内参、板位姿和工件坐标都不能复用。

## 2. 采集内参图片

使用明确的只读采集命令，将至少 25 张 ChArUco 图片写入一个新的、空的本机目录，例如：

```text
localstore/object-pose/hikvision-usb/charuco-intrinsics/
```

先将 `configs/hikvision-usb.example.json` 复制到同一相机的 `localstore/` 目录，填写真实设备的 `camera_id`、`calibration_id` 与 `camera_serial`。该文件包含本机设备绑定，不能提交；采集命令只读取其中的相机段。

```powershell
scripts\calibration.bat calibration-capture-charuco --config-file localstore/object-pose/hikvision-usb/camera.local.json --output-dir localstore/object-pose/hikvision-usb/charuco-intrinsics --frame-count 25 --capture-interval-seconds 3
```

此命令是整个标定 CLI 中唯一会在显式运行时打开相机的命令。它只读取 `camera`、`components.vision` 与 `components.vision_adapter_settings`，只构造并启动一个视觉适配器；不会读取或构造 `targets`、JAKA、夹爪、运行时或网页插件。每帧必须是健康的 `RGB8` 或 `Mono8` 规范像素，且整组采集的相机标识、分辨率和像素格式必须一致；任一帧异常会中止采集并在退出前关闭相机。目录必须为空，已成功写出的 PNG 会保留以便排错，不能与下一次采集混用。

图片必须满足以下条件：

- 来自同一相机设备绑定、同一分辨率、固定曝光/增益设置；
- 标定板覆盖画面中心、四角和边缘，并包含不同倾角；
- 每张图清晰，至少可见四个 ChArUco 角点；
- 不得混入截图、缩放图、裁切图或另一台同型号相机的图片。

采集过程中应由操作者移动标定板覆盖视场；相邻帧默认至少等待 `2 s`，建议现场使用 `3 s` 或更长间隔。命令拒绝小于 `0.5 s` 的间隔和超过 500 帧的采集，避免把快速重复画面误当作多视角标定集。该命令不改变曝光、增益、触发、用户集或其他相机参数。除执行这一显式命令外，不应打开相机。

## 3. 拟合相机内参

```powershell
scripts\calibration.bat calibration-camera-intrinsics --camera-id hikvision-usb --calibration-id hikvision-usb-calibration-20260729 --images-dir localstore/object-pose/hikvision-usb/charuco-intrinsics --output-file localstore/object-pose/hikvision-usb/camera-calibration.json
```

命令读取目录中支持的图片格式，跳过未检测到足够 ChArUco 角点的图片，并要求保留下来的视图不少于 25 张。它只会在重投影误差不大于 `0.5 px` 时写入内参 JSON；超限时不会产生输出文件。输出记录包含相机 ID、标定 ID、图像尺寸、相机矩阵、畸变参数、板规格和使用视图数。

## 4. 记录示教参考点

将标定板固定在最终工作位置后，操作者在示教器上手动触碰至少四个不共线的板上参考点，记录其对应的 JAKA 基座坐标。不得由本项目命令驱动机器人到这些点。

在 `localstore/object-pose/hikvision-usb/taught-points.json` 中记录配对数据。板坐标以毫米计，`board_point_mm.z_mm` 必须为 `0`，并与 ChArUco 板的物理坐标约定保持一致；每一项的两个点必须是一对对应点，数组顺序不能改变。

```json
{
  "correspondences": [
    {
      "board_point_mm": { "x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0 },
      "jaka_base_point_mm": { "x_mm": 412.3, "y_mm": -156.8, "z_mm": 24.1 }
    },
    {
      "board_point_mm": { "x_mm": 150.0, "y_mm": 0.0, "z_mm": 0.0 },
      "jaka_base_point_mm": { "x_mm": 412.7, "y_mm": -6.6, "z_mm": 24.0 }
    },
    {
      "board_point_mm": { "x_mm": 0.0, "y_mm": 100.0, "z_mm": 0.0 },
      "jaka_base_point_mm": { "x_mm": 312.2, "y_mm": -157.1, "z_mm": 24.2 }
    },
    {
      "board_point_mm": { "x_mm": 150.0, "y_mm": 100.0, "z_mm": 0.0 },
      "jaka_base_point_mm": { "x_mm": 312.5, "y_mm": -6.9, "z_mm": 24.0 }
    }
  ]
}
```

以上数字仅为 JSON 格式示例，不可用于真实工位。

## 5. 建立完整工作单元标定

在相机、标定板和照明均已固定后，用同一分辨率拍摄一张清晰的完整 ChArUco 板图并保存到 `localstore/`。它用于求 `board -> camera`，不是工件图片。

```powershell
scripts\calibration.bat calibration-build-workcell --camera-calibration-file localstore/object-pose/hikvision-usb/camera-calibration.json --board-image-file localstore/object-pose/hikvision-usb/fixed-board.png --taught-points-file localstore/object-pose/hikvision-usb/taught-points.json --output-file localstore/object-pose/hikvision-usb/workcell-calibration.json
```

命令在以下条件全部满足时才写出 `WorkcellCalibration`：

- 板图分辨率与内参图像尺寸完全一致；
- 板图包含足够 ChArUco 角点；
- 内参重投影误差不大于 `0.5 px`；
- 至少四个非共线示教点可拟合刚体变换；
- `board -> jaka_base` 拟合 RMS 不大于 `1 mm`。

输出同时保存变换、参考点数、RMS 和最大残差。运行时会再次绑定 `camera_id`、`calibration_id` 和画面尺寸；任一项变化都必须重新建立该相机的背景和标定，不能把另一台同型号相机的文件复制过来使用。

## 6. 接入对象识别配置

从 `configs/object-pose-preview.example.json` 复制一份到 `localstore/`，填写本机 `camera_id`、`calibration_id`、空桌背景文件、完整工作单元标定文件和已量取工件档案。模板默认关闭 `object_pose.enabled`，且不应在未完成上述验收前开启。

启用时档案必须填写 `nominal_length_mm`、`nominal_width_mm` 和 `object_thickness_mm`，并以实测值设置 `grasp_height_mm`、`grasp_origin_offset_x_mm/y_mm` 与 `maximum_planar_dimension_error_ratio`。抓取原点相对轮廓中心定义：`x` 沿头尾正方向，`y` 向左；非零偏移会强制要求可区分头尾。运行时将轮廓投影到板平面并复核名义尺寸，超出容差时返回 `planarity_suspected`，该原因涵盖倾斜疑似、遮挡、粘连和错误物体，且不会发布基座坐标。

首期视觉直接观测的是台面平面上的 `X/Y/Yaw`。`Z/Roll/Pitch` 只能由已知台面、工件厚度和标定约束推导；它们不是单目相机测得的任意 6D 姿态。工件档案默认使用 `directional_feature: none`，会返回 `pi` 周期偏航并拒绝单向夹取。只有现场量取、样本复验后明确配置 `directional_feature: larger_end`，且两端外侧轮廓占据量差超过 `minimum_directional_asymmetry` 时，才输出 `2*pi` 周期的完整偏航；证据不足时仍必须拒绝。

## 复验与失效条件

现场启用前至少完成：空桌连续 300 帧零误检；工件在不少于 30 个位置与多个朝向下，95% 的位置误差不超过 `±2 mm`、偏航误差不超过 `±3°`；遮挡、反光碎裂、粘连、非平放和背景变化均应返回无效原因而非抓取位姿。

出现相机切换、镜头/焦距/分辨率调整、相机或板重装、照明/台面显著变化、板移动或工件档案变化时，必须停止使用旧结果并重新采集背景及相关标定。该模块不授予任何自动运动权限；后续动作层仍需独立的工作空间、时效性、置信度和人工安全门验证。
