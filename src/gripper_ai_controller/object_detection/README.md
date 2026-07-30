# 通用二维目标检测

本包为相机预览提供统一的二维语义检测边界。`object-detection-analysis` 只消费采集循环已经发布到内存的 `FrameCaptured`/`ImageFrame`，输出类别、置信度和归一化外接框，用于在画面中标注可能的物品。它不持有相机 SDK、JAKA、夹爪、安全策略或命令权限，也不输出抓取点、机器人坐标或动作命令。

## 与已知工件位姿的区别

- `object-detection-analysis` 面向通用类别或导出时固化的一组提示类别，结果是二维语义框。它可以帮助筛选候选物体，但不能单独作为机械臂抓取位姿。
- `object-pose-analysis` 面向固定相机、固定台面和单个已知平放工件，依赖空桌背景、工件几何档案和工作单元标定，直接观测 `X/Y/Yaw`，并在台面约束下推导 `Z/Roll/Pitch`。

两者是独立 Plugin，可以分别启用、重置和重载，但共用网页服务唯一的逻辑相机、采集循环和 `FrameHub`。页面在 Plugin 详情中显示的相机选择器操作同一份全局相机目录；选择物理设备会切换整个预览管线，而不是为某个 Plugin 建立独占相机或第二条采集链路。

## 公共契约

- `DetectionProvider.infer(frame)`：同步分析一个 RGB8 或 Mono8 内存帧，返回零到多个 `DetectionCandidate`。
- `DetectionCandidate`：包含类别名、可选类别索引、置信度和源画面归一化外接框。
- `DetectionModelProfile`：声明稳定的 `model_id`、提供器、本地模型路径、阈值和提供器专属参数。
- `ObjectDetectionTrackingService`：使用单工作线程、限频和最新帧优先策略；慢推理期间只保留一个可替换的最新待处理帧。

## 已实现提供器

### Faster R-CNN

`TorchvisionFasterRcnnProvider` 使用项目锁定的 `torch==1.13.1` 与 `torchvision==0.14.1`，构造固定的 Faster R-CNN ResNet50-FPN 架构并加载显式指定的本地状态字典。类别按 COCO 顺序解释，`allowed_labels` 可进一步过滤结果。构造提供器时不导入 Torch，首次推理才读取权重；模型和 backbone 都以 `weights=None` 创建，因此运行服务不会自动访问网络下载权重。

COCO 类别可用于验证通用框选链路，但不包含扳手、钳子、螺丝刀等现场工具类别。它可能把相似金属工具映射成剪刀、勺子等已有类别，因此不能把 COCO 标签直接作为后续抓取语义；正确工具类别应使用提示词已固化的 YOLO-World ONNX 或经现场数据训练并验收的专用模型。

### YOLO-World ONNX

`YoloWorldOnnxOpenCvProvider` 复用项目锁定的 OpenCV DNN 加载本地 ONNX，不增加 `onnxruntime` 或 `ultralytics` 运行依赖。首期只支持**提示类别在导出前已经固化**的模型：`class_names` 必须与导出时类别顺序完全一致，运行时不会加载文本编码器，也不会把浏览器输入直接作为开放词汇提示。

[YOLO-World 官方仓库](https://github.com/AILab-CVC/YOLO-World)采用 GPL-3.0 许可证。当前项目只提供独立的本地 ONNX/OpenCV 推理接口，不复制该仓库代码、不附带其模型文件；部署方在取得、转换或分发模型前仍需自行核对模型来源、许可证与商用要求。

支持三种显式 `output_format`：

- `ultralytics`：原始输出为 `[1, 4 + 类别数, anchors]` 或其转置，由本项目执行置信度过滤和按类别 NMS。
- `end2end`：每行必须为 `[x1, y1, x2, y2, confidence, class_id]`。
- `official-nms`：用于官方带 NMS 的导出图，OpenCV 输出名称必须完整且精确为 `num_dets`、`boxes`、`scores`、`labels`。

三种格式都按固定 `input_size` 做 letterbox，并将框反算为源画面的归一化坐标。OpenCV 后端必须显式选择 `cpu`、`cuda` 或 `cuda-fp16`；请求 CUDA 后端但本机 OpenCV 不支持时会返回加载或推理错误，不会静默回退到 CPU。

## 模型文件与选择

仓库和默认配置**不包含可用权重或 ONNX 文件**。官方 Faster R-CNN COCO 权重可由操作者显式安装：

```powershell
scripts\run.bat object-detection-download-fasterrcnn
```

默认目标为 `localstore/models/fasterrcnn_resnet50_fpn_coco.pth`；可通过 `--weights-file` 选择另一个仍位于 `localstore/` 的相对路径。命令按固定 SHA-256 校验完整文件，校验成功后才原子替换目标；已有文件校验通过时直接复用，不访问网络。YOLO-World ONNX 仍需由操作者从符合许可证和部署要求的来源取得并核对导出契约。网页服务和检测 Plugin 在运行期不会自动下载、转换或更新任何模型。

`install_faster_rcnn_coco_weights()` 也会在公共 API 边界重复执行相同限制：目标必须是 `localstore/` 下、不含绝对路径或 `..` 的文件路径，解析后仍须位于该目录。不得依赖 CLI 校验来保护其他调用方。

所有 `model_path` 都只能是 `localstore/` 下不含绝对路径和 `..` 的相对路径。

`object_detection.selected_model_id` 定义进程启动时的模型。网页只能在当前配置已经声明且本地文件存在的模型之间切换；切换会等待旧模型工作结束并清空旧框，但只对当前服务进程会话生效，不会改写 JSON。服务重启或 Plugin 重载后重新采用配置中的 `selected_model_id`。接口契约见 [通用二维目标检测接口](../../../docs/object-detection-api.md)。

## 验证

在项目根目录运行：

```powershell
scripts\test.bat tests.test_object_detection_configuration tests.test_object_detection_providers tests.test_object_detection_tracking tests.test_object_detection_web tests.test_object_detection_weights -v
```

自动化测试使用假 Torch 张量、假 OpenCV 网络和本地临时文件，覆盖配置拒绝、输出解析、阈值、类别过滤、letterbox 反算、类别感知 NMS、Mono8、最新帧替换、模型切换和只读接口。测试不下载模型、不访问 GPU、不打开物理相机，也不连接机器人或夹爪。真实模型兼容性、现场类别准确率和抓取可用性仍需另行只读验收，不能由这些测试替代。
