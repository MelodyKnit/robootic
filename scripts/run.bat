@echo off
setlocal EnableExtensions DisableDelayedExpansion

call :require_execution_directory
if errorlevel 1 exit /b %ERRORLEVEL%
call :require_poetry_185
if errorlevel 1 exit /b %ERRORLEVEL%

if "%~1"=="" (
    call :fail 6ZSZ6K+v77ya5b+F6aG75oyH5a6aIENMSSDlrZDlkb3ku6TjgILnvZHpobXmnI3liqHpnIDopoHmmL7lvI/nmoQgLS1jb25maWctZmlsZeOAgg
    exit /b 1
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
    call :fail 6ZSZ6K+v77ya5ZG95Luk5omn6KGM5aSx6LSl44CC
    echo [ERROR] Exit code: %EXIT_CODE%
    exit /b %EXIT_CODE%
)

endlocal
exit /b 0

:require_execution_directory
if not exist "pyproject.toml" (
    call :fail 6ZSZ6K+v77ya6K+35ZyoIGdyaXBwZXItYWktY29udHJvbGxlciDpobnnm67moLnnm67lvZXmiafooYzmraTohJrmnKzjgII
    exit /b 1
)
if not exist "scripts\README.md" (
    call :fail 6ZSZ6K+v77ya5b2T5YmN55uu5b2V5LiN5piv5a6M5pW055qEIGdyaXBwZXItYWktY29udHJvbGxlciDpobnnm67moLnnm67lvZXjgII
    exit /b 1
)
exit /b 0

:require_poetry_185
where poetry >nul 2>nul
if errorlevel 1 (
    call :fail 6ZSZ6K+v77ya5pyq5om+5YiwIFBvZXRyeeOAgumhueebruimgeaxgiBQb2V0cnkgMS44LjXjgII
    exit /b 1
)
poetry --version | findstr /C:"Poetry (version 1.8.5)" >nul
if errorlevel 1 (
    call :fail 6ZSZ6K+v77ya5b2T5YmNIFBvZXRyeSDniYjmnKzkuI3lj5fmlK/mjIHjgILpobnnm67opoHmsYIgUG9ldHJ5IDEuOC4177yM5LiN6IO95L2/55SoIFBvZXRyeSAyLngg6K+75YaZIHBvZXRyeS5sb2Nr44CC
    exit /b 1
)
exit /b 0

:require_web_config
:scan_web_arguments
if "%~1"=="" (
    call :fail 6ZSZ6K+v77ya572R6aG15pyN5Yqh5b+F6aG75pi+5byP5o+Q5L6bIC0tY29uZmlnLWZpbGUg6YWN572u6Lev5b6E44CC
    exit /b 1
)
if /I "%~1"=="--config-file" goto config_file_argument
shift
goto scan_web_arguments

:config_file_argument
if "%~2"=="" (
    call :fail 6ZSZ6K+v77yaLS1jb25maWctZmlsZSDlkI7lv4Xpobvmj5DkvpvphY3nva7mlofku7bot6/lvoTjgII
    exit /b 1
)
if not exist "%~2" (
    call :fail 6ZSZ6K+v77ya5oyH5a6a55qE6YWN572u5paH5Lu25LiN5a2Y5Zyo44CC
    exit /b 1
)
exit /b 0

:fail
rem Decode ASCII Base64 so Chinese diagnostics survive cmd code-page differences.
powershell -NoProfile -Command "$encoded = '%~1'; if (($encoded.Length -band 3) -eq 2) { $encoded += '==' } elseif (($encoded.Length -band 3) -eq 3) { $encoded += '=' }; $message = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)); [Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::WriteLine($message)"
exit /b 0
