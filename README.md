# Supertonic TTS — Local WebSocket Server & Web UI

> A small toolkit I built **on top of** [Supertone's Supertonic](https://github.com/supertone-inc/supertonic) to turn the TTS engine into a local WebSocket server with a feature-rich browser client. Everything in this section lives in [`tool/`](tool/) and is **not part of the upstream project**.

<p align="center">
  <img src="img/main.png" alt="Supertonic TTS Web UI">
</p>

---

## ✨ Highlights

- 🚀 **One-click launcher** — `start_tts_server.bat` auto-installs deps and boots the server.
- ⚡ **GPU-accelerated** — auto-detects CUDA → DirectML → CPU. No config needed.
- 🧠 **Batch inference** — multiple requests within a 100 ms window are merged into a single GPU call, so 6 lines render as fast as 1.
- 🎭 **Multi-line dialogue queue** — assign a different voice/language per line for true conversational TTS.
- 📥 **Bulk import** — load dialogue from `.xlsx`, `.csv`, `.txt`, or markdown tables.
- 📦 **Batch ZIP download** — convert N lines, get N `.wav` files in one zip.
- 🔌 **Open WebSocket API** — drop-in client examples for Browser, Chrome MV3 extension, Node.js, Python.
- 🔄 **Upstream sync helper** — `sync_upstream.bat` keeps the fork in lock-step with `supertone-inc/supertonic`.

---

## 📁 What's in `tool/`

| File / Folder | Description |
|---|---|
| [`ws_tts_server.py`](tool/ws_tts_server.py) | WebSocket TTS server. Auto-detects GPU (CUDA / DirectML), batches concurrent requests, retries on GPU failure, and handles client disconnect gracefully. |
| [`tts_web.html`](tool/tts_web.html) | Standalone browser client. Multi-line dialogue queue, per-line voice/lang, file import, sequential auto-play, batch ZIP export. No build step. |
| [`start_tts_server.bat`](tool/start_tts_server.bat) | One-click Windows launcher. Auto-runs `uv sync` on first launch, then starts the server with banner + usage hints. |
| [`sync_upstream.bat`](tool/sync_upstream.bat) | Pulls latest `upstream/main`, merges into local `main`, and pushes to `origin`. Detects merge conflicts. |
| [`WEBSOCKET_API.md`](tool/WEBSOCKET_API.md) | Full protocol spec + integration guides (vanilla JS, Chrome MV3 extension with offscreen audio, Node, Python). |
| [`samples/`](tool/samples/) | Ready-to-import dialogue samples (`.csv`, `.md` table, `.txt` with `F1: text` prefix format). |

---

## 🚀 Quick Start (Windows)

**1. Set up the upstream project first** — only needed once, see [Upstream Setup](#-upstream-setup) below.

**2. Start the server:**

```bat
cd tool
start_tts_server.bat
```

The server listens on `ws://127.0.0.1:8765`. First launch warms up the GPU (≈10 s); subsequent inferences are sub-second.

**3. Open the web UI:**

Double-click [`tool/tts_web.html`](tool/tts_web.html) — it connects automatically.

---

## 🌐 Web UI Features (`tts_web.html`)

A self-contained HTML page (no build, no server). Open with any modern browser.

### Multi-line dialogue queue
- Each row has its own **Voice** and **Language** selector — perfect for two-speaker dialogues, narration with character voices, or multi-language announcements.
- Visual highlight on the currently playing row.
- `Enter` adds a new row below; `Backspace` on an empty row deletes it.

### Bulk input
| Action | How |
|---|---|
| **Paste multi-line** | Click `📋 Paste multi-line`. Each pasted line becomes a row. Supports `F1: hello` / `M2\|vi: xin chào` shorthand to set voice + lang inline. |
| **Import file** | Click `📥 Import`. Supports `.xlsx` (via SheetJS), `.csv`, `.md` markdown tables, and plain `.txt`. |
| **Voice assignment modes** | *Use default* / *Alternate F1↔M1* (auto-dialogue) / *From column* (read from imported file). |

### Output
- **Convert All (Ctrl+Enter)** — synthesizes the whole queue, plays back sequentially with the chosen playback speed (`preservesPitch = true`, so no chipmunk effect).
- **Per-line audio player** with individual `⬇` download button (`01_F1_en_Hello.wav`).
- **⬇ Download All (zip)** — bundles everything into `tts_batch_YYYY-MM-DD.zip`.
- **⏹ Stop** — halts mid-batch and cancels playback.

### Connection status
- Live indicator dot (green = connected, red = disconnected).
- Auto-reconnect on close with 2 s backoff.

---

## 🖥️ Server Features (`ws_tts_server.py`)

### GPU auto-detection
At startup, the server probes ONNX Runtime providers in this order:
1. **CUDA** (NVIDIA) — picks up DLLs from the `.venv/Lib/site-packages/nvidia/*` folders automatically.
2. **DirectML** (any DX12 GPU on Windows) — sets `enable_mem_pattern=False` and sequential execution to dodge the DirectML command-queue overflow bug.
3. **CPU** fallback.

Pass `--cpu` to skip GPU detection entirely.

### Batch inference
- Incoming requests are pushed into an `asyncio.Queue`.
- A worker waits **100 ms** to collect a batch, groups by voice (style), then runs **one** ONNX inference for the whole group.
- A global `asyncio.Lock` serializes batches → DirectML stays happy.
- Retry up to 3× on transient GPU errors before surfacing an error to the client.

### Robustness
- **Warmup on boot** with `total_step=4` then `total_step=8` to avoid DirectML's first-run timeout.
- **Disconnect-safe**: if a client drops mid-inference, the server discards its result and keeps serving others.
- **Echo `text` in audio_meta** so clients can pair binary frames with their original requests when many are in flight.

### CLI

```bat
uv run --project ../py python ws_tts_server.py [--port 8765] [--cpu]
```

| Flag | Meaning |
|---|---|
| `--port N` | Override the WebSocket port (default `8765`, env `WS_PORT`). |
| `--cpu` | Force CPU inference and skip GPU detection. |

---

## 🔌 WebSocket API (at a glance)

**Connect:** `ws://127.0.0.1:8765`

**Handshake (server → client, on connect):**
```json
{ "status": "connected", "voices": ["F1", ..., "M5"], "languages": ["en", "vi", "ko", ...] }
```

**Request (client → server):**
```json
{ "text": "Hello world", "lang": "en", "voice": "M1", "speed": 1.05 }
```

**Response:** for each request, the server sends two paired frames:
1. JSON `audio_meta` — `{ type, text, duration, latency_ms, size }`
2. Binary frame — a complete WAV file (16-bit PCM mono).

**Options:**
- `voice`: `M1–M5` (male), `F1–F5` (female) — case-insensitive, falls back to `M1`.
- `speed`: `0.25 – 4.0` (apply on client via `audio.playbackRate`; `preservesPitch = true`).
- `lang`: `en`, `vi`, `ko`, `ja`, `fr`, `de`, `es`, `pt`, `it`, … (31 languages).
- Inline markers in `text`: `<laugh>`, `<breath>`, `<sigh>`, plus standard punctuation for natural pauses.

Full spec with Chrome MV3 / Node / Python examples → [`tool/WEBSOCKET_API.md`](tool/WEBSOCKET_API.md).

---

## 📦 Upstream Setup

The server reuses the upstream Python runtime in [`py/`](py/) and the ONNX assets in `assets/`. You need these once:

```bash
# 1. Download ONNX models + preset voices (requires git-lfs)
git lfs install
git clone https://huggingface.co/Supertone/supertonic-3 assets

# 2. Install Python deps for the upstream runtime
cd py
uv sync
cd ..
```

After that, `start_tts_server.bat` handles the rest.

### Keeping in sync with upstream

If you fork this repo, [`tool/sync_upstream.bat`](tool/sync_upstream.bat) automates the maintenance loop:

```bat
cd tool
sync_upstream.bat
```

It runs `git fetch upstream` → `git merge upstream/main` → `git push origin main`, and stops cleanly if it detects a conflict so you can resolve it manually.

---

## About Upstream Supertonic

[**Supertonic**](https://github.com/supertone-inc/supertonic) by Supertone Inc. is a lightning-fast, on-device TTS system powered by ONNX Runtime. It runs fully offline, supports **31 languages**, and ships with ready-to-use examples in Python, Node.js, Browser, Java, C++, C#, Go, Swift, Rust, iOS, and Flutter (see the corresponding folders in this repo).

- **Models & demo:** [Hugging Face — Supertonic 3](https://huggingface.co/Supertone/supertonic-3) · [Interactive Demo](https://huggingface.co/spaces/Supertone/supertonic-3)
- **Python SDK:** `pip install supertonic` — docs at [supertone-inc.github.io/supertonic-py](https://supertone-inc.github.io/supertonic-py)
- **Upstream README & citations:** see the [original repository](https://github.com/supertone-inc/supertonic) for architecture details, benchmarks, paper citations, and per-language examples.

## License

- Sample code (including `tool/`): **MIT** — see [`LICENSE`](LICENSE).
- ONNX model weights: **OpenRAIL-M** — see the [model license on Hugging Face](https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE).

Upstream © 2026 Supertone Inc. Additions in `tool/` © their respective author.
