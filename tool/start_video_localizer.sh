#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_DIR=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
PY_DIR="$REPO_DIR/py"
PORT="${VIDEO_PORT:-8787}"

echo "============================================"
echo "  Supertonic Video Localizer"
echo "============================================"
echo
echo "  URL:      http://127.0.0.1:$PORT"
echo "  Output:   Soft subtitles"
echo "  Target:   Vietnamese"
echo
echo "  Translation requires CEREBRAS_API_KEY."
echo
echo "============================================"
echo

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[error] ffmpeg is required but was not found in PATH." >&2
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

exec "$PYTHON" "$SCRIPT_DIR/video_pipeline_server.py" --port "$PORT" "$@"

