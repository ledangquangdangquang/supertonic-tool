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


def run_command(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> None:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise MediaToolError(
            f"Command timed out after {timeout}s: {' '.join(args)}"
        )
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


def _has_audio_stream(video_path: Path) -> bool:
    """Return True if the video contains at least one audio stream."""
    require_tool("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "json",
            str(video_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return False
    try:
        data = json.loads(proc.stdout)
        return len(data.get("streams", [])) > 0
    except Exception:
        return False


def extract_audio(video_path: Path, audio_path: Path, timeout: int = 300) -> None:
    require_tool("ffmpeg")
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    if not _has_audio_stream(video_path):
        duration = _video_duration(video_path)
        run_command(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono",
                "-t", f"{duration:.6f}",
                "-c:a", "pcm_s16le",
                str(audio_path),
            ],
            timeout=timeout,
        )
        return

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
        ],
        timeout=timeout,
    )


def _video_duration(video_path: Path) -> float:
    """Return duration in seconds from ffprobe."""
    require_tool("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        return float(proc.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def mux_soft_subtitles(video_path: Path, subtitle_path: Path, output_path: Path, timeout: int = 300) -> None:
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
            str(output_path),
        ],
        timeout=timeout,
    )


def burn_subtitles(video_path: Path, subtitle_path: Path, output_path: Path, timeout: int = 3600) -> None:
    """Render subtitles into the video frames and omit selectable subtitle tracks."""
    require_tool("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path.resolve()),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            f"subtitles=filename={subtitle_path.name}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ac",
            "2",
            str(output_path.resolve()),
        ],
        cwd=subtitle_path.parent,
        timeout=timeout,
    )
