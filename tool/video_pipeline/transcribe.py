from __future__ import annotations

import os
from pathlib import Path

from .subtitle import SubtitleBlock, seconds_to_srt_time, write_srt


class TranscriptionError(RuntimeError):
    pass


class FasterWhisperTranscriber:
    def __init__(self, model_name: str = "small", device: str | None = None, compute_type: str | None = None):
        self.model_name = model_name
        self.device = device or os.environ.get("WHISPER_DEVICE", "cpu")
        self.compute_type = compute_type or os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

    def transcribe_to_srt(
        self,
        audio_path: Path,
        srt_path: Path,
        source_lang: str = "auto",
    ) -> list[SubtitleBlock]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "Missing faster-whisper. Install dependencies with `uv sync --project py`."
            ) from exc

        kwargs = {}
        if source_lang and source_lang != "auto":
            kwargs["language"] = source_lang

        blocks = self._run_transcription(WhisperModel, audio_path, kwargs)

        srt_path.write_text(write_srt(blocks), encoding="utf-8")
        return blocks

    def _run_transcription(self, whisper_model, audio_path: Path, kwargs: dict) -> list[SubtitleBlock]:
        try:
            model = whisper_model(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            segments, _info = model.transcribe(
                str(audio_path),
                vad_filter=True,
                beam_size=5,
                **kwargs,
            )
            return [
                SubtitleBlock(
                    index=i,
                    start=seconds_to_srt_time(segment.start),
                    end=seconds_to_srt_time(segment.end),
                    text=segment.text.strip(),
                )
                for i, segment in enumerate(segments, 1)
                if segment.text.strip()
            ]
        except Exception as exc:
            if self.device != "cpu" and _looks_like_accelerator_error(str(exc)):
                self.device = "cpu"
                self.compute_type = "int8"
                return self._run_transcription(whisper_model, audio_path, kwargs)
            raise TranscriptionError(str(exc)) from exc


def _looks_like_accelerator_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "cuda",
            "cublas",
            "cudnn",
            "gpu",
            "libcublas",
            "libcudnn",
        )
    )
