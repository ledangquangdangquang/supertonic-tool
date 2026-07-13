@echo off
setlocal EnableDelayedExpansion

title Supertonic Video Localizer
echo ============================================
echo   Supertonic Video Localizer
echo ============================================
echo.
echo   URL:      http://127.0.0.1:8787
echo   Output:   Soft subtitles
echo   Target:   Vietnamese
echo.
echo   Translation requires CEREBRAS_API_KEY.
echo.
echo ============================================
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [error] ffmpeg is required but was not found in PATH.
    pause
    exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "REPO_DIR=%SCRIPT_DIR%.."
set "PY_DIR=%REPO_DIR%\py"

cd /d "%PY_DIR%"

where uv >nul 2>nul
if errorlevel 1 (
    echo [error] uv is required but was not found in PATH.
    echo [error] Install uv, then run this launcher again.
    pause
    exit /b 1
)

echo [setup] Syncing dependencies with uv...
uv sync
if errorlevel 1 (
    echo [error] Dependency setup failed.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [error] Python executable not found after setup: .venv\Scripts\python.exe
    pause
    exit /b 1
)

.venv\Scripts\python.exe "%~dp0video_pipeline_server.py" --port 8787 %*

pause
