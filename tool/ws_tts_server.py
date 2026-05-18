"""
Supertonic TTS WebSocket Server
================================
Usage:
    cd supertonic/tool
    uv run --project ../py python ws_tts_server.py
    uv run --project ../py python ws_tts_server.py --port 8765 --cpu
    uv run --project ../py python ws_tts_server.py --provider cuda

Env vars:
    WS_PORT  - WebSocket port (default: 8765)
    TTS_PROVIDER - auto, cpu, cuda, directml, or coreml (default: auto)
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


def _site_packages_dirs():
    venv = _PY_DIR / ".venv"
    candidates = [venv / "Lib" / "site-packages"]
    candidates.extend((venv / "lib").glob("python*/site-packages"))
    candidates.extend((venv / "lib64").glob("python*/site-packages"))
    return [p for p in candidates if p.is_dir()]


def _prepend_env_path(key, value):
    existing = os.environ.get(key, "")
    parts = [p for p in existing.split(os.pathsep) if p]
    value_str = str(value)
    if value_str not in parts:
        os.environ[key] = value_str + (os.pathsep + existing if existing else "")


def _prepare_native_library_paths():
    """Expose CUDA libraries installed inside the venv when available."""
    for site_packages in _site_packages_dirs():
        nvidia_root = site_packages / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for package_dir in nvidia_root.iterdir():
            for subdir in ("bin", "lib"):
                native_dir = package_dir / subdir
                if not native_dir.is_dir():
                    continue
                if os.name == "nt":
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(str(native_dir))
                    _prepend_env_path("PATH", native_dir)
                elif sys.platform.startswith("linux"):
                    _prepend_env_path("LD_LIBRARY_PATH", native_dir)


_prepare_native_library_paths()

sys.path.insert(0, str(_PY_DIR))
from helper import load_voice_style, AVAILABLE_LANGS
import onnxruntime as ort
import soundfile as sf

if (os.name == "nt" or sys.platform.startswith("linux")) and hasattr(ort, "preload_dlls"):
    try:
        ort.preload_dlls(directory="")
    except Exception as e:
        print(f"[WARN] Could not preload ONNX Runtime native libraries: {e}")


def _require_assets(onnx_dir, voice_dir):
    required = [
        onnx_dir / "duration_predictor.onnx",
        onnx_dir / "text_encoder.onnx",
        onnx_dir / "vector_estimator.onnx",
        onnx_dir / "vocoder.onnx",
        onnx_dir / "tts.json",
        onnx_dir / "unicode_indexer.json",
    ]
    missing = [p for p in required if not p.is_file()]
    if missing:
        names = "\n  - ".join(str(p) for p in missing)
        raise RuntimeError(
            "Missing ONNX assets. Download the model assets first:\n"
            "  git lfs install\n"
            "  git clone https://huggingface.co/Supertone/supertonic-3 assets\n"
            f"\nMissing files:\n  - {names}"
        )
    if not voice_dir.is_dir() or not any(voice_dir.glob("*.json")):
        raise RuntimeError(f"Missing voice styles in: {voice_dir}")


def _session_options(providers):
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    if "DmlExecutionProvider" in providers:
        # DirectML needs these to avoid GPU command queue overflow.
        opts.enable_mem_pattern = False
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return opts


def _provider_candidates(provider_mode):
    available = set(ort.get_available_providers())
    cpu = ("CPU", ["CPUExecutionProvider"])
    providers = {
        "cuda": ("GPU (CUDA)", "CUDAExecutionProvider"),
        "directml": ("GPU (DirectML)", "DmlExecutionProvider"),
        "coreml": ("Accelerator (CoreML)", "CoreMLExecutionProvider"),
    }

    provider_mode = provider_mode.lower()
    if provider_mode == "cpu":
        return [cpu]

    if provider_mode in providers:
        label, provider = providers[provider_mode]
        if provider in available:
            return [(label, [provider, "CPUExecutionProvider"]), cpu]
        print(f"[WARN] Requested provider is not available: {provider}")
        return [cpu]

    ordered = [
        providers["cuda"],
        providers["directml"],
        providers["coreml"],
    ]
    candidates = [
        (label, [provider, "CPUExecutionProvider"])
        for label, provider in ordered
        if provider in available
    ]
    candidates.append(cpu)
    return candidates


def _load_tts(onnx_dir, provider_mode):
    """Load TTS with best-effort acceleration and CPU fallback."""
    from helper import TextToSpeech, load_cfgs, load_onnx_all, load_text_processor

    last_error = None
    for label, providers in _provider_candidates(provider_mode):
        opts = _session_options(providers)
        try:
            print(f"Using {label}")
            cfgs = load_cfgs(onnx_dir)
            dp_ort, text_enc_ort, vector_est_ort, vocoder_ort = load_onnx_all(
                onnx_dir, opts, providers
            )
            text_processor = load_text_processor(onnx_dir)
            return (
                TextToSpeech(
                    cfgs,
                    text_processor,
                    dp_ort,
                    text_enc_ort,
                    vector_est_ort,
                    vocoder_ort,
                ),
                label,
            )
        except Exception as e:
            last_error = e
            if providers == ["CPUExecutionProvider"]:
                break
            print(f"[WARN] Failed to initialize {label}: {e}")
            print("[WARN] Trying the next available provider...")

    raise RuntimeError(f"Failed to load TTS runtime: {last_error}")


# --- TTS Engine --- #
class TTSEngine:
    def __init__(self, provider_mode="auto"):
        onnx_dir = _REPO_DIR / "assets" / "onnx"
        voice_dir = _REPO_DIR / "assets" / "voice_styles"

        _require_assets(onnx_dir, voice_dir)
        self.tts, self.provider_name = _load_tts(str(onnx_dir), provider_mode)
        self.voices = {}
        for f in voice_dir.glob("*.json"):
            self.voices[f.stem] = load_voice_style([str(f)])
        self.languages = AVAILABLE_LANGS

        self._warmup(provider_mode)

    def _warmup(self, provider_mode):
        style = self.voices.get("M1", list(self.voices.values())[0])
        try:
            # Low step count first to avoid accelerator timeouts on first run.
            self.tts("warmup", "en", style, 4, 1.05)
            self.tts("warmup", "en", style, 8, 1.05)
        except Exception as e:
            if provider_mode == "auto" and self.provider_name != "CPU":
                print(f"[WARN] Warmup failed on {self.provider_name}: {e}")
                print("[WARN] Reloading with CPU fallback...")
                self.tts, self.provider_name = _load_tts(str(_REPO_DIR / "assets" / "onnx"), "cpu")
                self.tts("warmup", "en", style, 4, 1.05)
                self.tts("warmup", "en", style, 8, 1.05)
            else:
                raise

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
                    wav, dur = self.tts.batch(texts, langs, batch_style, total_step=8, speed=speed)
                    break
                except Exception as e:
                    if attempt < 2:
                        print(f"[WARN] Inference error (attempt {attempt+1}), retrying...")
                        time.sleep(0.5)
                    else:
                        raise RuntimeError(f"Inference failed after 3 attempts: {e}")

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
                    speed = max(0.7, min(2.0, speed))
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
    parser.add_argument(
        "--provider",
        choices=("auto", "cpu", "cuda", "directml", "coreml"),
        default=os.environ.get("TTS_PROVIDER", "auto").lower(),
        help="Inference provider to prefer. Default: auto.",
    )
    parser.add_argument("--cpu", action="store_true", help="Shortcut for --provider cpu.")
    args = parser.parse_args()
    provider_mode = "cpu" if args.cpu else args.provider

    print("Loading TTS engine...")
    engine = TTSEngine(provider_mode=provider_mode)
    print(f"Provider: {engine.provider_name}")
    print(f"Voices: {engine.voice_names}")

    server = WSTTSServer(engine, args.port)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
