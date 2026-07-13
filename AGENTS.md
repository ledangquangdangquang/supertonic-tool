# Agent Guide

This repository is a fork/extension of Supertone's Supertonic examples. The local product direction is to grow the existing TTS helper tool into a practical video localization workflow:

```text
video file -> extract audio -> generate SRT -> translate to Vietnamese -> review/edit -> mux or burn subtitles into video
```

Keep changes focused on the local tooling under `tool/` unless the user explicitly asks to modify the upstream SDK examples.

## Current Local Tooling

- `tool/ws_tts_server.py` is the local WebSocket TTS server.
- `tool/tts_web.html` is a standalone browser TTS client with a dialogue queue, import support, playback, and ZIP export.
- `tool/start_tts_server.bat` and `tool/start_tts_server.sh` are the user-facing launchers.
- `tool/WEBSOCKET_API.md` documents the current TTS WebSocket protocol.
- `README.md` describes the local tool layer on top of the upstream Supertonic repo.

The current web UI supports importing `.xlsx`, `.csv`, `.txt`, `.md`, and `.srt`. SRT import should parse subtitle text only, skipping numeric indexes and timecodes.

## Product Direction

The next major feature should be a video subtitle/localization pipeline. The intended user flow:

1. User selects a video file (`.mp4`, `.mov`, `.mkv`, etc.).
2. The app extracts audio with `ffmpeg`.
3. A speech-to-text engine generates an original-language `.srt`.
4. The `.srt` is translated to Vietnamese while preserving subtitle numbering and timestamps.
5. The user reviews and edits the subtitles in a video + subtitle editor UI.
6. The app exports either:
   - a video with soft subtitles, or
   - a burned-in subtitle video.

Do not jump straight to dubbing unless the user asks for Vietnamese voice-over. Subtitle localization is the MVP.

## Recommended MVP Plan

Build the video feature in small milestones:

1. **MVP 1: Video to SRT**
   - Accept a local video file.
   - Extract audio using `ffmpeg`.
   - Run transcription with Whisper/faster-whisper/whisper.cpp.
   - Export `original.srt`.

2. **MVP 2: Translate SRT to Vietnamese**
   - Parse SRT blocks safely.
   - Translate only subtitle text.
   - Preserve indexes and timestamps exactly.
   - Export `vi.srt`.

3. **MVP 3: Mux Soft Subtitles**
   - Add `vi.srt` back into the video as a selectable subtitle track.
   - Prefer stream copy where possible.
   - Export `output.mp4`.

4. **MVP 4: Burn-In Subtitles**
   - Render Vietnamese subtitles directly onto the video.
   - Add style controls: font, size, position, color, outline, and safe margins.
   - Provide a preview before rendering.

5. **Later: Vietnamese Dubbing**
   - Generate Vietnamese TTS per subtitle segment.
   - Fit or time-stretch audio to subtitle duration when needed.
   - Mix with original background audio if available.
   - This is more complex than subtitles and should be treated as a separate phase.

## Suggested Architecture

Prefer a Python backend for the video pipeline, because the repo already uses Python for runtime tooling and TTS.

Suggested layout:

```text
tool/
  video_localizer_web.html
  video_pipeline_server.py
  jobs/
    <job_id>/
      input.mp4
      audio.wav
      original.srt
      vi.srt
      output.mp4

tool/video_pipeline/
  audio_extract.py
  transcribe.py
  subtitle.py
  translate.py
  render.py
  jobs.py
```

Keep pipeline modules small and testable. The UI should communicate job progress through HTTP polling, Server-Sent Events, or WebSocket messages.

## Core Commands To Use

Audio extraction:

```bash
ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 audio.wav
```

Soft subtitle muxing for MP4:

```bash
ffmpeg -i input.mp4 -i vi.srt -c copy -c:s mov_text output.mp4
```

Burn-in subtitle rendering:

```bash
ffmpeg -i input.mp4 -vf subtitles=vi.srt output_burned.mp4
```

When paths may contain spaces or non-ASCII characters, escape/quote paths correctly. For burn-in subtitles on Windows, pay special attention to FFmpeg filter path escaping.

## Subtitle Handling Rules

- Preserve SRT indexes and timestamps unless the task explicitly changes timing.
- Translate text only.
- Keep multi-line subtitle blocks valid.
- Strip or handle simple formatting tags carefully (`<i>`, `<b>`, etc.).
- Avoid passing timestamp lines to TTS or translation.
- Prefer a structured SRT parser/writer when adding backend code. Avoid brittle string manipulation for anything beyond small UI import helpers.
- For long videos, process subtitles in batches with enough context for better translation consistency, then map translations back to the original blocks.

## Translation Strategy

The default target language is Vietnamese.

Possible translation backends:

- API-based: OpenAI, Google Translate, DeepL.
- Local: NLLB, MarianMT, Ollama-compatible models.

Do not hard-code a paid API as the only option. Design the translator interface so providers can be swapped.

Recommended interface:

```python
class Translator:
    def translate_blocks(self, blocks, source_lang="auto", target_lang="vi"):
        ...
```

## Frontend Direction

For the video feature, build an actual tool screen, not a marketing landing page.

Recommended layout:

```text
[video file picker / drop zone]

Source language: Auto
Target language: Vietnamese
Export mode: Soft subtitles | Burn into video

[Start]

Progress:
Extract audio -> Transcribe -> Translate -> Render

Preview:
Video player with subtitle overlay

Subtitle editor:
time | original text | Vietnamese text
```

The editor is important. Users should be able to fix translation mistakes before exporting the final video.

## Coding Guidelines

- Respect existing user changes. Do not revert unrelated work.
- Prefer `rg` for searching.
- Use `apply_patch` for manual edits.
- Keep the standalone HTML approach for simple tool pages unless a build step becomes clearly worthwhile.
- Keep docs updated when user-facing file formats, launch commands, or workflows change.
- Validate browser-facing JavaScript syntax after editing standalone HTML:

```bash
node - <<'NODE'
const fs = require('fs');
const html = fs.readFileSync('tool/tts_web.html', 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1])
  .filter(s => s.trim());
for (const script of scripts) new Function(script);
console.log('ok');
NODE
```

## Dependency Expectations

Likely dependencies for the video pipeline:

- `ffmpeg` available on PATH, or bundled/downloaded by launcher.
- A speech-to-text backend such as `faster-whisper`, `openai-whisper`, or `whisper.cpp`.
- Optional translation provider packages depending on the selected backend.

Before adding heavy dependencies, consider launcher behavior on Windows, Linux, and macOS.

## User Preference Notes

- The user wants the project to evolve from TTS tooling into video subtitle localization.
- The first localization target is Vietnamese.
- The user may use Vietnamese in conversation, but this guide must stay in English.
- When the user says "ghep lai vao video", clarify whether they mean soft subtitles, burned-in subtitles, or full voice dubbing if the distinction matters.
