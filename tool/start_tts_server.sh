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
echo "  Supertonic 3 WebSocket Server (31 langs)"
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
echo "  Voices: F1-F5 (female), M1-M5 (male)"
echo "  Speed:  0.7 (slow) - 2.0 (fast)"
echo "  Langs:  en vi ko ja fr de es pt it ... na (auto-detect)"
echo "  Expression tags: <laugh>, <breath>, <sigh>, ..."
echo
echo "============================================"
echo

# --- Auto-download model assets if missing ---
ASSETS_DIR="$REPO_DIR/assets"
ONNX_FILE="$ASSETS_DIR/onnx/duration_predictor.onnx"

if [ ! -f "$ONNX_FILE" ]; then
    echo "[assets] Model assets not found. Downloading Supertonic 3 from Hugging Face..."
    echo

    if ! command -v git >/dev/null 2>&1; then
        echo "[error] git is required to download model assets but was not found." >&2
        echo "[error] Install Git, then run this launcher again." >&2
        exit 1
    fi

    if ! git lfs version >/dev/null 2>&1; then
        echo "[setup] Installing Git LFS..."
        if ! git lfs install; then
            echo "[error] Failed to install Git LFS. Install manually: https://git-lfs.com" >&2
            exit 1
        fi
    fi

    if [ -e "$ASSETS_DIR" ]; then
        echo "[assets] Removing placeholder assets directory..."
        rm -rf "$ASSETS_DIR"
    fi

    if ! git clone https://huggingface.co/Supertone/supertonic-3 "$ASSETS_DIR"; then
        echo "[error] Failed to clone model assets. Check your internet connection." >&2
        exit 1
    fi
    echo "[assets] Download complete."
    echo
else
    echo "[assets] Model assets already present."
    echo
fi

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
