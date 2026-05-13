@echo off
title Supertonic TTS Server
echo ============================================
echo   Supertonic TTS WebSocket Server
echo ============================================
echo.
echo   Port:     8765
echo   GPU:      DirectML (auto)
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
uv run python "%~dp0ws_tts_server.py" %*

pause
