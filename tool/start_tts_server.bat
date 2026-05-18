@echo off
setlocal EnableDelayedExpansion

title Supertonic TTS Server S3
echo ============================================
echo   Supertonic 3 WebSocket Server (31 langs)
echo ============================================
echo.
echo   Port:     8765
echo   GPU:      Auto (CUDA ^> DirectML ^> CPU)
echo.
echo   Connect:  ws://127.0.0.1:8765
echo.
echo   Message:
echo   {"text":"...", "lang":"vi", "voice":"F1", "speed":1.05}
echo.
echo   Voices: F1-F5 (female), M1-M5 (male)
echo   Speed:  0.7 (slow) - 2.0 (fast)
echo   Langs:  en vi ko ja fr de es pt it ... na (auto-detect)
echo.
echo   Expression tags: ^<laugh^>, ^<breath^>, ^<sigh^>, ...
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "REPO_DIR=%SCRIPT_DIR%.."
set "PY_DIR=%REPO_DIR%\py"

:: --- Auto-download model assets if missing ---
if not exist "%REPO_DIR%\assets\onnx\duration_predictor.onnx" (
    echo [assets] Model assets not found. Downloading Supertonic 3 from Hugging Face...
    echo.

    where git >nul 2>nul
    if errorlevel 1 (
        echo [error] git is required to download model assets but was not found.
        echo [error] Install Git, then run this launcher again.
        pause
        exit /b 1
    )

    git lfs version >nul 2>nul
    if errorlevel 1 (
        echo [setup] Installing Git LFS...
        git lfs install
        if errorlevel 1 (
            echo [error] Failed to install Git LFS. Install manually: https://git-lfs.com
            pause
            exit /b 1
        )
    )

    if exist "%REPO_DIR%\assets" (
        echo [assets] Removing placeholder assets directory...
        rmdir /s /q "%REPO_DIR%\assets"
    )

    git clone https://huggingface.co/Supertone/supertonic-3 "%REPO_DIR%\assets"
    if errorlevel 1 (
        echo [error] Failed to clone model assets. Check your internet connection.
        pause
        exit /b 1
    )
    echo [assets] Download complete.
    echo.
) else (
    echo [assets] Model assets already present.
    echo.
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

.venv\Scripts\python.exe "%~dp0ws_tts_server.py" %*

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

.venv\Scripts\python.exe "%~dp0ws_tts_server.py" %*

pause
