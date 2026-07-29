@echo off
setlocal EnableExtensions DisableDelayedExpansion

call :require_execution_directory
if errorlevel 1 exit /b %ERRORLEVEL%
call scripts\check_poetry.bat
if errorlevel 1 exit /b %ERRORLEVEL%
poetry --version | findstr /R /C:"Poetry (version 2\." >nul
if not errorlevel 1 (
    echo [ERROR] Poetry 2.x cannot reliably bootstrap this Python 3.7 project.
    echo [ERROR] Use Poetry 1.7 or 1.8 for installation; Poetry 2.x remains supported for run and test.
    exit /b 1
)

echo [INFO] Installing project dependencies with Poetry...
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

:fail
rem Decode ASCII Base64 so Chinese diagnostics survive cmd code-page differences.
powershell -NoProfile -Command "$encoded = '%~1'; if (($encoded.Length -band 3) -eq 2) { $encoded += '==' } elseif (($encoded.Length -band 3) -eq 3) { $encoded += '=' }; $message = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)); [Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::WriteLine($message)"
exit /b 0
