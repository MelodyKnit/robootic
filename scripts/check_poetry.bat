@echo off
setlocal EnableExtensions EnableDelayedExpansion

where poetry >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Poetry was not found. Install Poetry 1.7+ or 2.x first.
    exit /b 1
)

set "POETRY_VERSION="
for /f "tokens=3" %%V in ('poetry --version 2^>nul') do set "POETRY_VERSION=%%V"
set "POETRY_VERSION=!POETRY_VERSION:)=!"

if not defined POETRY_VERSION (
    echo [ERROR] Unable to parse the installed Poetry version.
    exit /b 1
)

for /f "tokens=1,2 delims=." %%A in ("!POETRY_VERSION!") do (
    set "POETRY_MAJOR=%%A"
    set "POETRY_MINOR=%%B"
)

if "!POETRY_MAJOR!"=="1" (
    if !POETRY_MINOR! LSS 7 (
        echo [ERROR] Poetry !POETRY_VERSION! is too old. Use Poetry 1.7+ or 2.x.
        exit /b 1
    )
    echo [INFO] Poetry !POETRY_VERSION! is supported.
    exit /b 0
)

if "!POETRY_MAJOR!"=="2" (
    echo [INFO] Poetry !POETRY_VERSION! is supported.
    exit /b 0
)

echo [ERROR] Poetry !POETRY_VERSION! is outside the supported range: 1.7+ or 2.x.
exit /b 1
