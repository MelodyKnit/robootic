# 离线标定核心

本包的核心只处理已经获得的像素、ChArUco 角点和操作者示教的参考点；它不导入海康 SDK、JAKA SDK、夹爪客户端或网页服务，也不创建网络连接、打开相机、写入文件或发送机器人指令。`cli.py` 中唯一的例外是显式的 `calibration-capture-charuco`：它延迟导入并且只构造配置中的视觉适配器，以只读方式采集 PNG；它不会构造 JAKA、夹爪、运行时或插件。

## 坐标契约

首期固定使用 `DICT_5X5_100` 字典、`7 x 5` 方格、方格边长 `25 mm`、标记边长 `18 mm` 的 ChArUco 板。板平面定义为 `board.z = 0`；变换终点固定使用 `camera` 与 `jaka_base` 标准坐标系名称。运行时需要三份相互独立且同一设备绑定的本机标定数据：

- `CameraIntrinsics`：相机内参与畸变系数，适用于一个明确的图像尺寸；
- `board_to_camera`：由可见 ChArUco 板得到的板坐标到 OpenCV 相机坐标刚体变换；
- `board_to_jaka_base_fit`：操作者在示教器中触碰至少四个不共线板参考点后，由 `fit_board_to_jaka_base` 拟合得到的刚体变换、点数、RMS 残差和最大残差。

`WorkcellCalibration` 将三份数据和同一个 `calibration_id`、`camera_id` 组合为单个 JSON 文档。插件必须通过 `plane_projector_for_frame(camera_id, calibration_id, width, height)` 获取投影器；该方法会严格拒绝相机标识、标定标识或图像尺寸不一致的帧，并默认要求板到 JAKA 基座的拟合 RMS 不大于 `1.0 mm`。`PixelPlaneProjector` 再将像素射线与 `board.z = 0` 相交，并映射到 `jaka_base`。输出只有平面位置；它不从单目图像虚构物体高度、Roll、Pitch 或任意 6D 姿态。使用非零畸变系数投影、生成 ChArUco 板、内参标定和板姿态估计时，才会按需导入 `opencv-contrib-python-headless`。缺失时会抛出可操作的 `OpenCvUnavailableError`，不会导致网页预览或其他纯 2D 功能在导入阶段失败。

## 集成顺序

1. CLI 以显式输出路径在 `localstore/` 或 `temp/gripper-ai-controller/` 生成板图。需要采集时，操作者显式运行 `calibration-capture-charuco --config-file ... --output-dir localstore/... --frame-count 25`；该命令要求空目录、只启动一个视觉适配器，并在每次完成或失败后关闭它。
2. CLI 对每个已采集的内存帧调用 `detect_charuco_observation`；仅将有效角点组成 `CameraCalibrationInput`，调用 `calibrate_charuco_camera`，并把 `CameraCalibrationResult.to_dict()` 写入 `localstore/`。
3. 操作者以示教器完成参考点触碰；CLI 只读取输入点，并调用 `fit_board_to_jaka_base`。残差超过现场阈值时不得登记可用标定。
4. 对每个物理相机设备标识分别保存并绑定上述记录。相机切换、图像尺寸不匹配、标定 ID 缺失或参考点残差超限时，上层必须返回无效对象结果，不能产生 JAKA 基座坐标。

任何未来动作模块必须将本包输出视为只读感知数据，并另行通过工作空间、时效性、置信度与显式人工安全门验证；本包不具备运动授权能力。
