@echo off
setlocal EnableExtensions DisableDelayedExpansion

if not exist "pyproject.toml" (
    echo [ERROR] Run this script from the gripper-ai-controller project root.
    exit /b 1
)

call scripts\check_poetry.bat
if errorlevel 1 exit /b %ERRORLEVEL%

poetry --version | findstr /C:"Poetry (version 1.8.5)" >nul
if errorlevel 1 (
    echo [ERROR] poetry.lock must be maintained with Poetry 1.8.5.
    echo [ERROR] Newer Poetry versions may write a lock format that Poetry 1.x cannot read.
    exit /b 1
)

echo [INFO] Refreshing poetry.lock without updating resolved package versions...
poetry lock --no-update

set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    echo [ERROR] Lock refresh failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo [INFO] poetry.lock was refreshed with Poetry 1.8.5.
endlocal
exit /b 0
