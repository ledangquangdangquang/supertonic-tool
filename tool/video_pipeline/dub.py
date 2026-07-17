from __future__ import annotations

from pathlib import Path
import math
import re
import sys

import soundfile as sf

from .media import MediaToolError, probe_media, require_tool, run_command
from .subtitle import SubtitleBlock, srt_time_to_seconds


class DubbingError(RuntimeError):
    pass


def create_vietnamese_dub(
    video_path: Path,
    blocks: list[SubtitleBlock],
    work_dir: Path,
    output_path: Path,
    voice: str = "F1",
    speed: float = 1.05,
    provider: str = "cpu",
    background_volume: float = 0.0,
    voice_volume: float = 1.0,
) -> Path:
    """Generate Vietnamese TTS from subtitle blocks and replace or mix the video's audio."""
    if not blocks:
        raise DubbingError("No subtitle blocks available for dubbing.")

    clips_dir = work_dir / "dub_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    dub_audio = work_dir / "dub_vi.wav"

    engine = _load_tts_engine(provider)
    clip_paths: list[tuple[Path, int]] = []
    for block in blocks:
        text = _clean_tts_text(block.text)
        if not text:
            continue

        start_ms = int(round(srt_time_to_seconds(block.start) * 1000))
        target_duration = max(0.25, srt_time_to_seconds(block.end) - srt_time_to_seconds(block.start))
        wav_bytes, duration, _latency = engine.synthesize(text, "vi", voice, speed)
        if duration > target_duration * 1.08 and speed < 2.0:
            fitted_speed = min(2.0, speed * (duration / target_duration) * 1.03)
            wav_bytes, _duration, _latency = engine.synthesize(text, "vi", voice, fitted_speed)

        clip_path = clips_dir / f"{block.index:05d}.wav"
        clip_path.write_bytes(wav_bytes)
        clip_paths.append((clip_path, start_ms))

    if not clip_paths:
        raise DubbingError("No TTS clips were generated.")

    duration = _video_duration(video_path)
    _mix_delayed_clips(clip_paths, dub_audio, duration)
    if background_volume > 0:
        _mux_mixed_audio(video_path, dub_audio, output_path, background_volume, voice_volume)
    else:
        _mux_replace_audio(video_path, dub_audio, output_path, voice_volume)
    return output_path


def mux_vietnamese_dub_audio(
    video_path: Path,
    dub_audio_path: Path,
    output_path: Path,
    background_volume: float = 0.0,
    voice_volume: float = 1.0,
) -> Path:
    if background_volume > 0:
        _mux_mixed_audio(video_path, dub_audio_path, output_path, background_volume, voice_volume)
    else:
        _mux_replace_audio(video_path, dub_audio_path, output_path, voice_volume)
    return output_path


def _load_tts_engine(provider: str):
    tool_dir = Path(__file__).resolve().parents[1]
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    try:
        from ws_tts_server import TTSEngine
    except Exception as exc:
        raise DubbingError(f"Could not import Supertonic TTS engine: {exc}") from exc

    try:
        return TTSEngine(provider_mode=provider)
    except Exception as exc:
        raise DubbingError(f"Could not load Supertonic TTS engine: {exc}") from exc


def _clean_tts_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _video_duration(video_path: Path) -> float:
    media = probe_media(video_path)
    try:
        return float(media["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DubbingError("Could not determine video duration.") from exc


def _mix_delayed_clips(
    clips: list[tuple[Path, int]],
    output_path: Path,
    duration: float,
    chunk_seconds: float = 300.0,
) -> None:
    """Render a long TTS timeline in bounded chunks, then concatenate losslessly."""
    require_tool("ffmpeg")
    if duration <= 0:
        raise DubbingError("Video duration must be positive.")

    chunk_seconds = max(30.0, chunk_seconds)
    chunk_dir = output_path.parent / "dub_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    clip_info = [
        (path, start_ms / 1000.0, start_ms / 1000.0 + float(sf.info(path).duration))
        for path, start_ms in clips
    ]
    chunk_paths: list[Path] = []

    for chunk_index in range(math.ceil(duration / chunk_seconds)):
        chunk_start = chunk_index * chunk_seconds
        chunk_end = min(duration, chunk_start + chunk_seconds)
        chunk_duration = chunk_end - chunk_start
        active = [item for item in clip_info if item[1] < chunk_end and item[2] > chunk_start]
        chunk_path = chunk_dir / f"chunk_{chunk_index:05d}.wav"

        if active:
            _render_audio_chunk(active, chunk_path, chunk_start, chunk_duration)
        else:
            run_command(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", f"{chunk_duration:.6f}", "-c:a", "pcm_s16le", str(chunk_path),
                ]
            )
        chunk_paths.append(chunk_path)

    concat_list = chunk_dir / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{path.name}'\n" for path in chunk_paths),
        encoding="utf-8",
    )
    run_command(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:a", "copy", str(output_path),
        ]
    )
    concat_list.unlink(missing_ok=True)
    for chunk_path in chunk_paths:
        chunk_path.unlink(missing_ok=True)
    chunk_dir.rmdir()


def _render_audio_chunk(
    clips: list[tuple[Path, float, float]],
    output_path: Path,
    chunk_start: float,
    chunk_duration: float,
) -> None:
    args = ["ffmpeg", "-y"]
    for clip_path, _start, _end in clips:
        args.extend(["-i", str(clip_path)])

    filter_parts = []
    labels = []
    chunk_end = chunk_start + chunk_duration
    for index, (_path, clip_start, clip_end) in enumerate(clips):
        trim_start = max(0.0, chunk_start - clip_start)
        trim_end = min(clip_end, chunk_end) - clip_start
        delay_ms = max(0, int(round((clip_start - chunk_start) * 1000)))
        label = f"a{index}"
        labels.append(f"[{label}]")
        filter_parts.append(
            f"[{index}:a]atrim=start={trim_start:.6f}:end={trim_end:.6f},"
            f"asetpts=PTS-STARTPTS,adelay={delay_ms}:all=1[{label}]"
        )

    filter_parts.append(
        "".join(labels)
        + f"amix=inputs={len(clips)}:duration=longest:dropout_transition=0:normalize=0,"
        + f"apad,atrim=0:{chunk_duration:.6f},alimiter=limit=0.95[dub]"
    )
    args.extend(
        [
            "-filter_complex", ";".join(filter_parts), "-map", "[dub]",
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(output_path),
        ]
    )
    run_command(args)


def _mux_replace_audio(video_path: Path, audio_path: Path, output_path: Path, voice_volume: float) -> None:
    require_tool("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice_volume = max(0.1, min(5.0, voice_volume))
    try:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-map",
                "0:s?",
                "-filter:a",
                f"volume={voice_volume:.3f},alimiter=limit=0.95",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-c:s",
                "copy",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
    except MediaToolError as exc:
        raise DubbingError(str(exc)) from exc


def _mux_mixed_audio(
    video_path: Path,
    dub_audio_path: Path,
    output_path: Path,
    background_volume: float,
    voice_volume: float,
) -> None:
    require_tool("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    background_volume = max(0.0, min(1.0, background_volume))
    voice_volume = max(0.0, min(2.0, voice_volume))
    try:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(dub_audio_path),
                "-filter_complex",
                (
                    f"[0:a]volume={background_volume:.3f}[bg];"
                    f"[1:a]volume={voice_volume:.3f}[voice];"
                    "[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[mix]"
                ),
                "-map",
                "0:v:0",
                "-map",
                "[mix]",
                "-map",
                "0:s?",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-c:s",
                "copy",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
    except MediaToolError as exc:
        raise DubbingError(str(exc)) from exc
