from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


class MediaToolError(RuntimeError):
    pass


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaToolError(f"Missing required command: {name}")
    return path


def run_command(args: list[str]) -> None:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise MediaToolError(detail or f"Command failed: {' '.join(args)}")


def probe_media(video_path: Path) -> dict:
    require_tool("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise MediaToolError(proc.stderr.strip() or "ffprobe failed")
    return json.loads(proc.stdout)


def extract_audio(video_path: Path, audio_path: Path) -> None:
    require_tool("ffmpeg")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ]
    )


def mux_soft_subtitles(video_path: Path, subtitle_path: Path, output_path: Path) -> None:
    require_tool("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(subtitle_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map",
            "1:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ac",
            "2",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=vie",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
