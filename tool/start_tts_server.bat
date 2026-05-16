@echo off
title Supertonic TTS Server
echo ============================================
echo   Supertonic TTS WebSocket Server
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
echo   Voices: M1-M5 (male), F1-F5 (female)
echo   Speed:  0.25 (slow) - 4.0 (fast)
echo   Langs:  en vi ko ja fr de es pt it ...
echo.
echo ============================================
echo.

cd /d "%~dp0..\py"

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
