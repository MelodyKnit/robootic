@echo off
REM 视觉识别模块一键配置和启动脚本

setlocal enabledelayedexpansion

echo ========================================
echo 视觉识别模块配置向导
echo ========================================
echo.

:MENU
echo 请选择操作:
echo.
echo 1. 下载 YOLOv8 模型
echo 2. 配置物体检测 (Object Detection)
echo 3. 配置姿态识别 (Object Pose)
echo 4. 启动检测服务
echo 5. 启动完整服务 (检测+姿态)
echo 6. 查看配置文件
echo 7. 退出
echo.

set /p choice="请输入选项 [1-7]: "

if "%choice%"=="1" goto DOWNLOAD_MODELS
if "%choice%"=="2" goto CONFIGURE_DETECTION
if "%choice%"=="3" goto CONFIGURE_POSE
if "%choice%"=="4" goto START_DETECTION
if "%choice%"=="5" goto START_FULL
if "%choice%"=="6" goto VIEW_CONFIG
if "%choice%"=="7" goto END

echo 无效选项，请重新选择
echo.
goto MENU

:DOWNLOAD_MODELS
echo.
echo ========================================
echo 下载 YOLOv8 模型
echo ========================================
echo.
poetry run python scripts/download_yolov8_models.py
if errorlevel 1 (
    echo.
    echo 下载失败！请检查网络连接
    pause
) else (
    echo.
    echo 下载完成！
    pause
)
goto MENU

:CONFIGURE_DETECTION
echo.
echo ========================================
echo 配置物体检测
echo ========================================
echo.
poetry run python scripts/configure_yolov8_detection.py
if errorlevel 1 (
    echo.
    echo 配置失败！
    pause
) else (
    echo.
    echo 配置完成！
    pause
)
goto MENU

:CONFIGURE_POSE
echo.
echo ========================================
echo 配置姿态识别
echo ========================================
echo.
poetry run python scripts/setup_object_pose.py
if errorlevel 1 (
    echo.
    echo 配置失败！
    pause
) else (
    echo.
    echo 配置完成！
    echo.
    echo ⚠ 重要提示：
    echo 请拍摄空背景图并保存到：
    echo localstore\object-pose\hikvision-usb\empty-table.png
    pause
)
goto MENU

:START_DETECTION
echo.
echo ========================================
echo 启动物体检测服务
echo ========================================
echo.
echo 正在启动...
echo 浏览器访问: http://127.0.0.1:8000
echo.
poetry run gripper-ai-controller web --config-file localstore\hikvision-object-detection.local.json
goto MENU

:START_FULL
echo.
echo ========================================
echo 启动完整服务 (检测+姿态)
echo ========================================
echo.

REM 检查背景图是否存在
if not exist "localstore\object-pose\hikvision-usb\empty-table.png" (
    echo ⚠ 警告：未找到空背景图
    echo 姿态识别可能无法正常工作
    echo.
    set /p continue="是否继续启动? [Y/N]: "
    if /i not "!continue!"=="Y" goto MENU
)

echo 正在启动...
echo 浏览器访问: http://127.0.0.1:8000
echo.
poetry run gripper-ai-controller web --config-file localstore\hikvision-object-detection.local.json
goto MENU

:VIEW_CONFIG
echo.
echo ========================================
echo 查看配置文件
echo ========================================
echo.
echo 配置文件位置:
echo   localstore\hikvision-object-detection.local.json
echo.
echo 姿态识别配置:
echo   localstore\object-pose\hikvision-usb\
echo.
echo 模型文件:
echo   localstore\models\
echo.
if exist "localstore\models\yolov8n.onnx" (
    echo ✓ yolov8n.onnx
) else (
    echo ✗ yolov8n.onnx 不存在
)
if exist "localstore\models\yolov8s.onnx" (
    echo ✓ yolov8s.onnx
) else (
    echo ✗ yolov8s.onnx 不存在
)
echo.
if exist "localstore\object-pose\hikvision-usb\empty-table.png" (
    echo ✓ 空背景图已配置
) else (
    echo ⚠ 空背景图未配置
)
if exist "localstore\object-pose\hikvision-usb\workcell-calibration.json" (
    echo ✓ 标定文件已配置
) else (
    echo ⚠ 标定文件未配置
)
echo.
pause
goto MENU

:END
echo.
echo 感谢使用！
exit /b 0
