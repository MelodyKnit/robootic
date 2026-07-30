@echo off
setlocal EnableExtensions DisableDelayedExpansion

if not exist "pyproject.toml" (
    echo [ERROR] Run this command from the gripper-ai-controller project root.
    exit /b 1
)
if not exist "src\web\package.json" (
    echo [ERROR] Frontend package metadata is unavailable.
    exit /b 1
)

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=build"
if not "%~1"=="" shift

set "FORWARD_ARGS="
:collect_arguments
if "%~1"=="" goto select_action
if defined FORWARD_ARGS (
    set "FORWARD_ARGS=%FORWARD_ARGS% %1"
) else (
    set "FORWARD_ARGS=%1"
)
shift
goto collect_arguments

:select_action
if /I "%ACTION%"=="install" goto install
if /I "%ACTION%"=="build" goto build
if /I "%ACTION%"=="test" goto test

echo [ERROR] Usage: scripts\frontend.bat install ^| build ^| test [Playwright arguments]
exit /b 2

:install
if defined FORWARD_ARGS (
    echo [ERROR] The install action does not accept additional arguments.
    exit /b 2
)
pushd "src\web"
call pnpm install --frozen-lockfile
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:build
if defined FORWARD_ARGS (
    echo [ERROR] The build action does not accept additional arguments.
    exit /b 2
)
pushd "src\web"
call pnpm run build
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:test
pushd "src\web"
call pnpm exec playwright test %FORWARD_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
