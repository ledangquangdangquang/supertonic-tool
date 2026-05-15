"""
Supertonic TTS WebSocket Server
================================
Usage:
    cd supertonic/tool
    uv run --project ../py python ws_tts_server.py
    uv run --project ../py python ws_tts_server.py --port 8765 --cpu

Env vars:
    WS_PORT  - WebSocket port (default: 8765)
"""
import sys
import os
import io
import json
import time
import asyncio
from pathlib import Path

# Resolve paths relative to this file
_TOOL_DIR = Path(__file__).parent
_REPO_DIR = _TOOL_DIR.parent
_PY_DIR = _REPO_DIR / "py"

# Add NVIDIA CUDA DLLs from venv to search path
_VENV_NVIDIA = _PY_DIR / ".venv" / "Lib" / "site-packages" / "nvidia"
if _VENV_NVIDIA.is_dir():
    for d in _VENV_NVIDIA.iterdir():
        for sub in ("bin", "lib"):
            p = d / sub
            if p.is_dir():
                os.add_dll_directory(str(p))
                os.environ["PATH"] = str(p) + ";" + os.environ.get("PATH", "")

sys.path.insert(0, str(_PY_DIR))
from helper import load_voice_style, AVAILABLE_LANGS
import onnxruntime as ort
import soundfile as sf


def _load_tts_gpu(onnx_dir):
    """Load TTS with GPU support without modifying repo's helper.py"""
    from helper import TextToSpeech, UnicodeProcessor, load_cfgs, load_onnx_all, load_text_processor

    opts = ort.SessionOptions()
    opts.log_severity_level = 3  # suppress memcpy warnings
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        print("Using GPU (CUDA)")
    elif "DmlExecutionProvider" in available:
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        # DirectML needs these to avoid GPU command queue overflow
        opts.enable_mem_pattern = False
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        print("Using GPU (DirectML)")
    else:
        providers = ["CPUExecutionProvider"]
        print("Using CPU")

    cfgs = load_cfgs(onnx_dir)
    dp_ort, text_enc_ort, vector_est_ort, vocoder_ort = load_onnx_all(onnx_dir, opts, providers)
    text_processor = load_text_processor(onnx_dir)
    return TextToSpeech(cfgs, text_processor, dp_ort, text_enc_ort, vector_est_ort, vocoder_ort)


# --- TTS Engine --- #
class TTSEngine:
    def __init__(self, use_gpu=True):
        onnx_dir = str(_REPO_DIR / "assets" / "onnx")
        voice_dir = str(_REPO_DIR / "assets" / "voice_styles")

        self.tts = _load_tts_gpu(onnx_dir) if use_gpu else self._load_cpu(onnx_dir)
        self.voices = {}
        for f in os.listdir(voice_dir):
            if f.endswith(".json"):
                self.voices[f.replace(".json", "")] = load_voice_style(
                    [os.path.join(voice_dir, f)]
                )
        self.languages = AVAILABLE_LANGS

        # Warmup (low step count to avoid DML timeout on first run)
        style = self.voices.get("M1", list(self.voices.values())[0])
        self.tts("warmup", "en", style, 4, 1.05)
        time.sleep(1)
        self.tts("warmup", "en", style, 8, 1.05)

    def _load_cpu(self, onnx_dir):
        from helper import load_text_to_speech
        return load_text_to_speech(onnx_dir, use_gpu=False)

    @property
    def voice_names(self):
        return sorted(self.voices.keys())

    def synthesize(self, text, lang="en", voice="M1", speed=1.05):
        return self.synthesize_batch([(text, lang, voice, speed)])[0]

    def synthesize_batch(self, items):
        """items: list of (text, lang, voice, speed). Returns list of (wav_bytes, duration, latency)."""
        import numpy as np
        from helper import Style

        # Group by voice (batch requires same style)
        indexed = sorted(enumerate(items), key=lambda x: x[1][2])
        results = [None] * len(items)
        t0 = time.perf_counter()

        from itertools import groupby
        for voice_key, group in groupby(indexed, key=lambda x: x[1][2]):
            group = list(group)
            style = self.voices.get(voice_key.upper(), self.voices.get("M1"))
            texts, langs = [], []
            for idx, (text, lang, voice, speed) in group:
                lang = lang.split("-")[0]
                texts.append(text if lang in self.languages else text)
                langs.append(lang if lang in self.languages else "en")

            batch_style = Style(
                np.repeat(style.ttl, len(texts), axis=0),
                np.repeat(style.dp, len(texts), axis=0),
            )

            for attempt in range(3):
                try:
                    wav, dur = self.tts.batch(texts, langs, batch_style, total_step=8, speed=1.05)
                    break
                except Exception as e:
                    if attempt < 2:
                        print(f"[WARN] GPU error (attempt {attempt+1}), retrying...")
                        time.sleep(0.5)
                    else:
                        raise RuntimeError(f"GPU failed after 3 attempts: {e}")

            for j, (idx, _) in enumerate(group):
                n_samples = int(self.tts.sample_rate * dur[j].item())
                samples = wav[j, :n_samples]
                buf = io.BytesIO()
                sf.write(buf, samples, self.tts.sample_rate, format="WAV", subtype="PCM_16")
                results[idx] = (buf.getvalue(), n_samples / self.tts.sample_rate, time.perf_counter() - t0)

        return results


# --- WebSocket Server --- #
BATCH_WAIT_MS = 100  # collect requests for this long before batching

class WSTTSServer:
    def __init__(self, engine, port=8765):
        self.engine = engine
        self.port = port
        self._gpu_lock = asyncio.Lock()
        self._batch_queue = asyncio.Queue()
        self._batch_event = asyncio.Event()

    async def _batch_worker(self):
        """Collects requests, waits BATCH_WAIT_MS, then processes as batch."""
        while True:
            # Wait for first request
            first = await self._batch_queue.get()
            batch = [first]

            # Collect more requests within window
            deadline = asyncio.get_event_loop().time() + BATCH_WAIT_MS / 1000
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._batch_queue.get(), timeout=remaining)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            # Process batch
            async with self._gpu_lock:
                items = [(req["text"], req["lang"], req["voice"], req["speed"]) for _, req in batch]
                print(f"[BATCH] Processing {len(items)} items")
                loop = asyncio.get_event_loop()
                try:
                    results = await loop.run_in_executor(None, self.engine.synthesize_batch, items)
                except Exception as e:
                    # Send error to all clients
                    for (ws, req), _ in zip(batch, range(len(batch))):
                        if ws.close_code is None:
                            try:
                                await ws.send(json.dumps({"type": "error", "message": str(e)}))
                            except Exception:
                                pass
                    continue

                # Send results back
                for (ws, req), result in zip(batch, results):
                    if ws.close_code is not None or result is None:
                        continue
                    audio_bytes, duration, latency = result
                    try:
                        await ws.send(json.dumps({
                            "type": "audio_meta",
                            "text": req["text"],
                            "duration": round(duration, 3),
                            "latency_ms": round(latency * 1000),
                            "size": len(audio_bytes),
                        }))
                        await ws.send(audio_bytes)
                    except Exception:
                        pass

    async def handle_client(self, websocket):
        try:
            await websocket.send(json.dumps({
                "status": "connected",
                "voices": self.engine.voice_names,
                "languages": self.engine.languages,
            }))
        except Exception:
            return

        try:
            async for message in websocket:
                try:
                    req = json.loads(message)
                    text = req.get("text", "").strip()
                    if not text:
                        continue

                    speed = float(req.get("speed", 1.05))
                    speed = max(0.25, min(4.0, speed))
                    print(f"[TTS] lang={req.get('lang','en')} voice={req.get('voice','M1')} speed={speed} text={text[:40]}")

                    await self._batch_queue.put((websocket, {
                        "text": text,
                        "lang": req.get("lang", "en"),
                        "voice": req.get("voice", "M1"),
                        "speed": speed,
                    }))

                except Exception as e:
                    if websocket.close_code is not None:
                        break
        except Exception:
            pass
        print("[WS] Client disconnected")

    async def run(self):
        import websockets
        # Start batch worker
        asyncio.create_task(self._batch_worker())
        async with websockets.serve(
            self.handle_client, "127.0.0.1", self.port,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=10,
            max_size=10 * 1024 * 1024,
        ):
            print(f"WebSocket TTS ready: ws://127.0.0.1:{self.port}")
            await asyncio.Future()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("WS_PORT", 8765)))
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()

    print("Loading TTS engine...")
    engine = TTSEngine(use_gpu=not args.cpu)
    print(f"Voices: {engine.voice_names}")

    server = WSTTSServer(engine, args.port)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
