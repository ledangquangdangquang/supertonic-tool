"""
Video Localization Server
=========================
Usage:
    cd supertonic-tool
    uv run --project py python tool/video_pipeline_server.py

Environment:
    CEREBRAS_API_KEY   Required for translation.
    CEREBRAS_MODEL     Optional, defaults to gpt-oss-120b.
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

sys.path.insert(0, str(_TOOL_DIR))

from video_pipeline.dub import create_vietnamese_dub
from video_pipeline.media import extract_audio, mux_soft_subtitles, probe_media
from video_pipeline.subtitle import parse_srt, write_srt
from video_pipeline.transcribe import FasterWhisperTranscriber
from video_pipeline.translate import CerebrasTranslator


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


JOBS: dict[str, JobState] = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = time.time()


def _add_file(job_id: str, kind: str, path: Path) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.files = {**job.files, kind: str(path)}
        job.updated_at = time.time()


def _job_payload(job: JobState) -> dict:
    payload = asdict(job)
    payload["downloads"] = {
        name: f"/api/jobs/{job.id}/download/{name}"
        for name, path in job.files.items()
        if Path(path).is_file()
    }
    return payload


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"} else ".mp4"


@app.get("/", response_class=HTMLResponse)
def index():
    return (_TOOL_DIR / "video_localizer_web.html").read_text(encoding="utf-8")


@app.get("/api/config")
def config():
    return {
        "has_cerebras_api_key": bool(os.environ.get("CEREBRAS_API_KEY")),
        "cerebras_model": os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b"),
    }


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_lang: str = Form("auto"),
    target_lang: str = Form("vi"),
    whisper_model: str = Form("small"),
    export_mode: str = Form("soft"),
    translate: str = Form("true"),
    dub: str = Form("false"),
    tts_voice: str = Form("F1"),
):
    if export_mode != "soft":
        raise HTTPException(status_code=400, detail="Only soft subtitle export is implemented.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = _JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / f"input{_safe_suffix(file.filename or '')}"

    with input_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    state = JobState(
        id=job_id,
        files={"input": str(input_path)},
        options={
            "source_lang": source_lang,
            "target_lang": target_lang,
            "whisper_model": whisper_model,
            "export_mode": export_mode,
            "translate": translate,
            "dub": dub,
            "tts_voice": tts_voice,
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
    return FileResponse(path, filename=path.name)


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        input_path = Path(job.files["input"])
        opts = dict(job.options)

    job_dir = input_path.parent
    audio_path = job_dir / "audio.wav"
    original_srt = job_dir / "original.srt"
    vi_srt = job_dir / "vi.srt"
    output_video = job_dir / "output_soft_subtitles.mp4"
    output_dubbed_video = job_dir / "output_vietnamese_only.mp4"

    try:
        _set_job(job_id, status="running", step="Reading video metadata", progress=5)
        media = probe_media(input_path)
        _set_job(job_id, media=media)

        _set_job(job_id, step="Extracting audio", progress=15)
        extract_audio(input_path, audio_path)
        _add_file(job_id, "audio", audio_path)

        _set_job(job_id, step="Transcribing audio to SRT", progress=35)
        transcriber = FasterWhisperTranscriber(model_name=opts["whisper_model"])
        original_blocks = transcriber.transcribe_to_srt(
            audio_path,
            original_srt,
            source_lang=opts["source_lang"],
        )
        _add_file(job_id, "original_srt", original_srt)

        subtitle_for_export = original_srt
        if opts.get("translate", "true") == "true":
            _set_job(job_id, step="Translating subtitles to Vietnamese", progress=65)
            translator = CerebrasTranslator()
            vi_blocks = translator.translate_blocks(
                original_blocks,
                source_lang=opts["source_lang"],
                target_lang=opts["target_lang"],
            )
            vi_srt.write_text(write_srt(vi_blocks), encoding="utf-8")
            subtitle_for_export = vi_srt
            _add_file(job_id, "vi_srt", vi_srt)

        _set_job(job_id, step="Muxing soft subtitles into video", progress=85)
        mux_soft_subtitles(input_path, subtitle_for_export, output_video)
        _add_file(job_id, "output_video", output_video)

        if opts.get("dub", "false") == "true":
            _set_job(job_id, step="Generating Vietnamese voice-over", progress=90)
            dub_blocks = parse_srt(subtitle_for_export.read_text(encoding="utf-8"))
            create_vietnamese_dub(
                output_video,
                dub_blocks,
                job_dir,
                output_dubbed_video,
                voice=opts.get("tts_voice", "F1"),
                provider=os.environ.get("TTS_PROVIDER", "cpu"),
                background_volume=0.0,
                voice_volume=3.0,
            )
            _add_file(job_id, "output_dubbed_video", output_dubbed_video)

        _set_job(job_id, status="done", step="Done", progress=100)
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
