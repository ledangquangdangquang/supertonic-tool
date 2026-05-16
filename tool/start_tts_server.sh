#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_DIR=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
PY_DIR="$REPO_DIR/py"
PORT="${WS_PORT:-8765}"

UNAME_S=$(uname -s 2>/dev/null || echo unknown)
UNAME_M=$(uname -m 2>/dev/null || echo unknown)
case "$UNAME_S" in
    Darwin) OS_LABEL="macOS" ;;
    Linux) OS_LABEL="Linux" ;;
    *) OS_LABEL="$UNAME_S" ;;
esac

echo "============================================"
echo "  Supertonic TTS WebSocket Server"
echo "============================================"
echo
echo "  OS:       $OS_LABEL ($UNAME_M)"
echo "  Port:     $PORT"
echo "  GPU:      Auto (CUDA/Linux, CoreML/macOS if available, CPU fallback)"
echo
echo "  Connect:  ws://127.0.0.1:$PORT"
echo
echo "  Message:"
echo '  {"text":"...", "lang":"vi", "voice":"F1", "speed":1.05}'
echo
echo "  Voices: M1-M5 (male), F1-F5 (female)"
echo "  Speed:  0.25 (slow) - 4.0 (fast)"
echo "  Langs:  en vi ko ja fr de es pt it ..."
echo
echo "============================================"
echo

if [ ! -d "$PY_DIR" ]; then
    echo "[error] Python project folder not found: $PY_DIR" >&2
    exit 1
fi

cd "$PY_DIR"

if command -v uv >/dev/null 2>&1; then
    echo "[setup] Syncing dependencies with uv..."
    uv sync
    PYTHON="$PY_DIR/.venv/bin/python"
else
    echo "[setup] uv not found; using python3 + venv + pip fallback."
    if ! command -v python3 >/dev/null 2>&1; then
        echo "[error] python3 is required. Install Python 3.10+ or install uv, then run again." >&2
        exit 1
    fi
    if [ ! -x "$PY_DIR/.venv/bin/python" ]; then
        python3 -m venv "$PY_DIR/.venv"
    fi
    PYTHON="$PY_DIR/.venv/bin/python"
    "$PYTHON" -m pip install --upgrade pip setuptools wheel
    "$PYTHON" -m pip install -e .
fi

if [ ! -x "$PYTHON" ]; then
    echo "[error] Python executable not found after setup: $PYTHON" >&2
    exit 1
fi

if [ "$UNAME_S" = "Linux" ]; then
    NVIDIA_LIBS=$(find "$PY_DIR/.venv" -path '*/site-packages/nvidia/*/lib' -type d 2>/dev/null | paste -sd: - || true)
    if [ -n "$NVIDIA_LIBS" ]; then
        export LD_LIBRARY_PATH="$NVIDIA_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
fi

exec "$PYTHON" "$SCRIPT_DIR/ws_tts_server.py" "$@"
