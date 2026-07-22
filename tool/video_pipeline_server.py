"""
Video Localization Server
=========================
Usage:
    cd supertonic-tool
    uv run --project py python tool/video_pipeline_server.py

Environment:
    CEREBRAS_API_KEY   Required for translation.
    CEREBRAS_MODEL     Optional, defaults to gpt-oss-120b.
    OLLAMA_MODEL        Optional, defaults to hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M.
    OLLAMA_BASE_URL     Optional, defaults to http://127.0.0.1:11434.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

_TOOL_DIR = Path(__file__).parent
_REPO_DIR = _TOOL_DIR.parent
_JOBS_DIR = _TOOL_DIR / "jobs"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_JOBS = 50
JOB_TTL_SECONDS = 3600 * 6  # 6 hours

sys.path.insert(0, str(_TOOL_DIR))

from video_pipeline.dub import create_vietnamese_dub
from video_pipeline.media import burn_subtitles, extract_audio, mux_soft_subtitles, probe_media
from video_pipeline.subtitle import parse_srt, write_srt
from video_pipeline.transcribe import FasterWhisperTranscriber
from video_pipeline.translate import CerebrasTranslator, GoogleTranslator, OllamaTranslator


app = FastAPI(title="Supertonic Video Localizer")


@dataclass
class JobState:
    id: str
    status: str = "queued"
    step: str = "Waiting"
    progress: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    files: dict[str, str] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    media: dict | None = None
    step_timings: dict[str, float] = field(default_factory=dict)
    _current_step_start: float = field(default=0.0, repr=False)


JOBS: dict[str, JobState] = {}
JOBS_LOCK = threading.Lock()


def _cleanup_jobs() -> None:
    """Evict expired jobs and enforce MAX_JOBS limit."""
    now = time.time()
    with JOBS_LOCK:
        expired = [
            jid for jid, j in JOBS.items()
            if j.status in {"done", "error"} and (now - j.updated_at) > JOB_TTL_SECONDS
        ]
    for jid in expired:
        _delete_job(jid)

    with JOBS_LOCK:
        if len(JOBS) > MAX_JOBS:
            finished = [
                (jid, j.updated_at)
                for jid, j in JOBS.items()
                if j.status in {"done", "error"}
            ]
            finished.sort(key=lambda x: x[1])
            to_remove = finished[: len(finished) - MAX_JOBS + 5]
            for jid, _ in to_remove:
                _delete_job(jid)


def _delete_job(job_id: str) -> None:
    job_dir = None
    with JOBS_LOCK:
        job = JOBS.pop(job_id, None)
    if job:
        job_dir = Path(job.files.get("input", "")).parent
    if job_dir and job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)


def _set_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = time.time()


def _set_step(job_id: str, step: str, progress: int) -> None:
    """Record timing for the previous step and start a new one."""
    now = time.time()
    with JOBS_LOCK:
        job = JOBS[job_id]
        if job._current_step_start > 0:
            elapsed = now - job._current_step_start
            job.step_timings[job.step] = round(elapsed, 1)
        job.step = step
        job.progress = progress
        job._current_step_start = now
        job.updated_at = now


def _add_file(job_id: str, kind: str, path: Path) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.files = {**job.files, kind: str(path)}
        job.updated_at = time.time()


def _job_payload(job: JobState) -> dict:
    payload = asdict(job)
    payload.pop("_current_step_start", None)
    payload["downloads"] = {
        name: f"/api/jobs/{job.id}/download/{name}"
        for name, path in job.files.items()
        if Path(path).is_file()
    }
    total_elapsed = time.time() - job.created_at
    payload["total_elapsed"] = round(total_elapsed, 1)
    return payload


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"} else ".mp4"


def _mask_secret(value: str | None) -> str | None:
    """Return a safe status hint without exposing an API key to the browser."""
    if not value:
        return None
    if len(value) <= 8:
        return "Configured"
    return f"{value[:4]}…{value[-4:]}"


def _ollama_status() -> tuple[bool, bool, str | None]:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M")
    try:
        from urllib import request
        import json

        with request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        available_models = {item.get("name") for item in payload.get("models", [])}
        return True, model in available_models, None
    except Exception as exc:
        return False, False, str(exc)


@app.get("/", response_class=HTMLResponse)
def index():
    return (_TOOL_DIR / "video_localizer_web.html").read_text(encoding="utf-8")


@app.get("/tokens.css")
def design_tokens():
    return FileResponse(_REPO_DIR / "tokens.css", media_type="text/css")


@app.get("/api/config")
def config():
    server_api_key = os.environ.get("CEREBRAS_API_KEY")
    ollama_available, ollama_model_available, ollama_error = _ollama_status()
    return {
        "has_cerebras_api_key": bool(server_api_key),
        "cerebras_api_key_hint": _mask_secret(server_api_key),
        "cerebras_model": os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b"),
        "ollama_available": ollama_available,
        "ollama_model_available": ollama_model_available,
        "ollama_model": os.environ.get("OLLAMA_MODEL", "hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M"),
        "ollama_error": ollama_error,
    }


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_lang: str = Form("en"),
    target_lang: str = Form("vi"),
    whisper_model: str = Form("small"),
    export_mode: str = Form("soft"),
    translate: str = Form("true"),
    translation_provider: str = Form("ollama"),
    dub: str = Form("false"),
    tts_voice: str = Form("F1"),
    background_volume: float = Form(0.0),
    voice_volume: float = Form(1.0),
):
    if export_mode not in {"soft", "burn"}:
        raise HTTPException(status_code=400, detail="Unsupported subtitle export mode.")
    if translation_provider not in {"ollama", "cerebras", "google"}:
        raise HTTPException(status_code=400, detail="Unsupported translation provider.")

    _cleanup_jobs()

    content_length = file.size
    if content_length and content_length > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )

    job_id = uuid.uuid4().hex[:12]
    job_dir = _JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / f"input{_safe_suffix(file.filename or '')}"

    uploaded = 0
    with input_path.open("wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            uploaded += len(chunk)
            if uploaded > MAX_UPLOAD_BYTES:
                input_path.unlink(missing_ok=True)
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
                )
            out.write(chunk)

    original_stem = Path(file.filename or "input").stem
    state = JobState(
        id=job_id,
        files={"input": str(input_path)},
        _current_step_start=time.time(),
        options={
            "source_lang": source_lang,
            "target_lang": target_lang,
            "whisper_model": whisper_model,
            "export_mode": export_mode,
            "translate": translate,
            "translation_provider": translation_provider,
            "dub": dub,
            "tts_voice": tts_voice,
            "background_volume": str(background_volume),
            "voice_volume": str(voice_volume),
            "original_stem": original_stem,
        },
    )
    with JOBS_LOCK:
        JOBS[job_id] = state

    background_tasks.add_task(_run_job, job_id)
    return _job_payload(state)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_payload(job)


@app.get("/api/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        path = Path(job.files.get(kind, ""))

    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not ready")

    original_stem = job.options.get("original_stem", path.stem)
    download_names = {
        "output_burned_video": f"{original_stem}_vi_burned.mp4",
        "output_dubbed_video": f"{original_stem}_vi_dub.mp4",
        "output_video": f"{original_stem}_vi_soft.mp4",
        "vi_srt": f"{original_stem}_vi.srt",
        "original_srt": f"{original_stem}_original.srt",
        "audio": f"{original_stem}_audio.wav",
        "input": path.name,
    }
    filename = download_names.get(kind, path.name)
    return FileResponse(path, filename=filename)


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        input_path = Path(job.files["input"])
        opts = dict(job.options)

    job_dir = input_path.parent
    audio_path = job_dir / "audio.wav"
    original_srt = job_dir / "original.srt"
    vi_srt = job_dir / "vi.srt"
    input_stem = input_path.stem
    output_video = job_dir / f"{input_stem}_vi_soft.mp4"
    output_dubbed_video = job_dir / f"{input_stem}_vi_dub.mp4"
    output_burned_video = job_dir / f"{input_stem}_vi_burned.mp4"

    try:
        _set_step(job_id, "Reading video metadata", 5)
        with JOBS_LOCK:
            JOBS[job_id].status = "running"
        media = probe_media(input_path)
        _set_job(job_id, media=media)

        has_audio = any(
            s.get("codec_type") == "audio"
            for s in media.get("streams", [])
        )
        if not has_audio:
            _set_step(job_id, "Video has no audio track — generating silence for pipeline", 5)

        _set_step(job_id, "Extracting audio", 15)
        extract_audio(input_path, audio_path)
        _add_file(job_id, "audio", audio_path)

        _set_step(job_id, "Transcribing audio to SRT", 35)
        transcriber = FasterWhisperTranscriber(model_name=opts["whisper_model"])
        original_blocks, detected_lang = transcriber.transcribe_to_srt(
            audio_path,
            original_srt,
            source_lang=opts["source_lang"],
        )
        _add_file(job_id, "original_srt", original_srt)

        if not original_blocks:
            _set_step(job_id, "No speech detected in audio — SRT will be empty", 35)

        subtitle_for_export = original_srt
        if opts.get("translate", "true") == "true":
            _set_step(job_id, "Translating subtitles to Vietnamese", 65)
            provider = opts.get("translation_provider", "ollama")
            if provider == "google":
                translator = GoogleTranslator()
            elif provider == "cerebras":
                translator = CerebrasTranslator()
            else:
                translator = OllamaTranslator()
            vi_blocks = translator.translate_blocks(
                original_blocks,
                source_lang=opts["source_lang"],
                target_lang=opts["target_lang"],
            )
            vi_srt.write_text(write_srt(vi_blocks), encoding="utf-8")
            subtitle_for_export = vi_srt
            _add_file(job_id, "vi_srt", vi_srt)

        _set_step(job_id, "Muxing soft subtitles into video", 85)
        mux_soft_subtitles(input_path, subtitle_for_export, output_video)
        _add_file(job_id, "output_video", output_video)

        if opts.get("dub", "false") == "true":
            _set_step(job_id, "Generating Vietnamese voice-over", 90)
            dub_blocks = parse_srt(subtitle_for_export.read_text(encoding="utf-8"))
            bg_vol = float(opts.get("background_volume", "0.0"))
            vc_vol = float(opts.get("voice_volume", "1.0"))
            create_vietnamese_dub(
                output_video,
                dub_blocks,
                job_dir,
                output_dubbed_video,
                voice=opts.get("tts_voice", "F1"),
                provider=os.environ.get("TTS_PROVIDER", "cpu"),
                background_volume=bg_vol,
                voice_volume=vc_vol,
            )
            _add_file(job_id, "output_dubbed_video", output_dubbed_video)

        if opts.get("export_mode") == "burn":
            _set_step(job_id, "Burning Vietnamese subtitles into video", 96)
            burn_source = output_dubbed_video if opts.get("dub", "false") == "true" else input_path
            burn_subtitles(burn_source, subtitle_for_export, output_burned_video)
            _add_file(job_id, "output_burned_video", output_burned_video)

        # finalize: record last step timing
        _set_step(job_id, "Done", 100)
        _set_job(job_id, status="done")
    except Exception as exc:
        _set_job(job_id, status="error", step="Failed", error=str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    import uvicorn

    print(f"Video localizer: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
