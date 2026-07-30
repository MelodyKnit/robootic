@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

call scripts\check_poetry.bat
if errorlevel 1 exit /b %ERRORLEVEL%

if /I "%~1"=="--dev" (
    set "DEV_MODE=1"
    shift
)

set "CONFIG_PATH=configs/development.json"
if exist "localstore\hikvision-web.local.json" (
    set "CONFIG_PATH=localstore/hikvision-web.local.json"
)

if "%DEV_MODE%"=="1" (
    echo [INFO] Running in DEVELOPMENT mode with config: %CONFIG_PATH%
    echo [INFO] Running: poetry run python -m gripper_ai_controller web --config-file %CONFIG_PATH%
    poetry run python -m gripper_ai_controller web --config-file %CONFIG_PATH%
    exit /b %ERRORLEVEL%
)

if "%~1"=="" (
    set "CONFIG_PATH=configs/development.json"
    if exist "localstore\hikvision-web.local.json" (
        set "CONFIG_PATH=localstore/hikvision-web.local.json"
    )
    echo [INFO] Starting Web control with default config: %CONFIG_PATH%
    echo [INFO] Running: poetry run python -m gripper_ai_controller web --config-file %CONFIG_PATH%
    poetry run python -m gripper_ai_controller web --config-file %CONFIG_PATH%
    exit /b %ERRORLEVEL%
)

set "COMMAND_NAME=%~1"
set "COMMAND_ARGS=%*"
if /I "%COMMAND_NAME%"=="web" (
    call :require_web_config %*
    if errorlevel 1 exit /b 1
)

echo [INFO] Running: poetry run python -m gripper_ai_controller %COMMAND_ARGS%
poetry run python -m gripper_ai_controller %COMMAND_ARGS%

set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    echo [ERROR] Command failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

endlocal
exit /b 0

:require_web_config
:scan_web_arguments
if "%~1"=="" (
    echo [ERROR] Web startup requires an explicit --config-file path.
    exit /b 1
)
if /I "%~1"=="--config-file" goto config_file_argument
shift
goto scan_web_arguments

:config_file_argument
if "%~2"=="" (
    echo [ERROR] --config-file requires a path value.
    exit /b 1
)
if not exist "%~2" (
    echo [ERROR] The configured file does not exist: %~2
    exit /b 1
)
exit /b 0
