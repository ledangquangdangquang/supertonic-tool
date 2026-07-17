#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_DIR=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
PY_DIR="$REPO_DIR/py"
ASSETS_DIR="$REPO_DIR/assets"
ONNX_FILE="$ASSETS_DIR/onnx/duration_predictor.onnx"
PORT="${VIDEO_PORT:-8787}"

echo "============================================"
echo "  Supertonic Video Localizer"
echo "============================================"
echo
echo "  URL:      http://127.0.0.1:$PORT"
echo "  Output:   Soft subtitles"
echo "  Target:   Vietnamese"
echo
echo "  Translation: local Ollama (qwen3:4b) or Cerebras API."
echo
echo "============================================"
echo

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[error] ffmpeg is required but was not found in PATH." >&2
    exit 1
fi

# Git LFS pointer files are small text files, not usable ONNX models.
if [ ! -f "$ONNX_FILE" ] || grep -q '^version https://git-lfs.github.com/spec/' "$ONNX_FILE"; then
    echo "[assets] Downloading the real Supertonic ONNX models..."
    if ! command -v git >/dev/null 2>&1 || ! git lfs version >/dev/null 2>&1; then
        echo "[error] Git and Git LFS are required to download model assets." >&2
        exit 1
    fi
    git lfs install
    if [ -d "$ASSETS_DIR/.git" ]; then
        git -C "$ASSETS_DIR" lfs pull
    else
        rm -rf "$ASSETS_DIR"
        git clone https://huggingface.co/Supertone/supertonic-3 "$ASSETS_DIR"
    fi
    if [ ! -f "$ONNX_FILE" ] || grep -q '^version https://git-lfs.github.com/spec/' "$ONNX_FILE"; then
        echo "[error] Model download did not complete. Check the network connection and disk space." >&2
        exit 1
    fi
    echo "[assets] Model download complete."
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

exec "$PYTHON" "$SCRIPT_DIR/video_pipeline_server.py" --port "$PORT" "$@"
