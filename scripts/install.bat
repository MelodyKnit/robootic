@echo off
setlocal EnableExtensions DisableDelayedExpansion

call :require_execution_directory
if errorlevel 1 exit /b %ERRORLEVEL%
call :require_poetry_185
if errorlevel 1 exit /b %ERRORLEVEL%

echo [INFO] Installing project dependencies with Poetry 1.8.5...
if "%~1"=="" (
    poetry install
) else (
    poetry install %*
)

set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    call :fail 6ZSZ6K+v77ya5L6d6LWW5a6J6KOF5aSx6LSl44CC
    echo [ERROR] Exit code: %EXIT_CODE%
    exit /b %EXIT_CODE%
)

echo [INFO] Project dependencies installed.
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

:fail
rem Decode ASCII Base64 so Chinese diagnostics survive cmd code-page differences.
powershell -NoProfile -Command "$encoded = '%~1'; if (($encoded.Length -band 3) -eq 2) { $encoded += '==' } elseif (($encoded.Length -band 3) -eq 3) { $encoded += '=' }; $message = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)); [Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::WriteLine($message)"
exit /b 0
