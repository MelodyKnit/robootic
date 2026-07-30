@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

call scripts\check_poetry.bat
if errorlevel 1 exit /b %ERRORLEVEL%

if "%~1"=="" (
    call :usage
    exit /b 2
)

set "COMMAND_NAME=%~1"
if /I "%COMMAND_NAME%"=="calibration-generate-charuco" goto offline
if /I "%COMMAND_NAME%"=="calibration-camera-intrinsics" goto offline
if /I "%COMMAND_NAME%"=="calibration-build-workcell" goto offline
if /I "%COMMAND_NAME%"=="calibration-capture-charuco" goto capture

echo [ERROR] Unsupported calibration command: %COMMAND_NAME%
call :usage
exit /b 2

:offline
echo [INFO] Running offline calibration command. No camera, JAKA, or gripper connection is created.
goto execute

:capture
call :require_capture_config %*
if errorlevel 1 exit /b %ERRORLEVEL%
echo [INFO] Running explicit read-only ChArUco capture. It permits only the configured camera connection.

:execute
echo [INFO] No calibration command constructs or controls JAKA or the gripper.
poetry run python -m gripper_ai_controller %*
exit /b %ERRORLEVEL%

:require_capture_config
:scan_capture_arguments
if "%~1"=="" (
    echo [ERROR] calibration-capture-charuco requires an explicit --config-file path.
    exit /b 2
)
if /I "%~1"=="--config-file" goto capture_config_argument
shift
goto scan_capture_arguments

:capture_config_argument
if "%~2"=="" (
    echo [ERROR] --config-file requires a path value.
    exit /b 2
)
if not exist "%~2" (
    echo [ERROR] The configured file does not exist: %~2
    exit /b 2
)
exit /b 0

:usage
echo [ERROR] Allowed commands: calibration-generate-charuco, calibration-capture-charuco,
echo [ERROR] calibration-camera-intrinsics, calibration-build-workcell.
exit /b 0
