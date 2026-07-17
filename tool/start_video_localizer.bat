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
echo   Translation: local Ollama (qwen3:4b) or Cerebras API.
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
set "ASSETS_DIR=%REPO_DIR%\assets"
set "ONNX_FILE=%ASSETS_DIR%\onnx\duration_predictor.onnx"

set "NEED_ASSETS=0"
if not exist "%ONNX_FILE%" set "NEED_ASSETS=1"
if exist "%ONNX_FILE%" (
    findstr /b /c:"version https://git-lfs.github.com/spec/" "%ONNX_FILE%" >nul 2>nul
    if not errorlevel 1 set "NEED_ASSETS=1"
)
if "!NEED_ASSETS!"=="1" (
    echo [assets] Downloading the real Supertonic ONNX models...
    where git >nul 2>nul
    if errorlevel 1 (
        echo [error] Git and Git LFS are required to download model assets.
        pause
        exit /b 1
    )
    git lfs install
    if exist "%ASSETS_DIR%\.git" (
        git -C "%ASSETS_DIR%" lfs pull
    ) else (
        if exist "%ASSETS_DIR%" rmdir /s /q "%ASSETS_DIR%"
        git clone https://huggingface.co/Supertone/supertonic-3 "%ASSETS_DIR%"
    )
    if errorlevel 1 (
        echo [error] Model download failed. Check the network connection and disk space.
        pause
        exit /b 1
    )
)

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
