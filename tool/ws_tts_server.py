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

sys.path.insert(0, str(_PY_DIR))
from helper import load_voice_style, AVAILABLE_LANGS
import onnxruntime as ort
import soundfile as sf


def _load_tts_gpu(onnx_dir):
    """Load TTS with GPU support without modifying repo's helper.py"""
    from helper import TextToSpeech, UnicodeProcessor, load_cfgs, load_onnx_all, load_text_processor

    opts = ort.SessionOptions()
    available = ort.get_available_providers()
    if "DmlExecutionProvider" in available:
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        print("Using GPU (DirectML)")
    elif "CUDAExecutionProvider" in available:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        print("Using GPU (CUDA)")
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

        # Warmup
        style = self.voices.get("M1", list(self.voices.values())[0])
        self.tts("warmup", "en", style, 8, 1.05)
        self.tts("warmup", "en", style, 8, 1.05)

    def _load_cpu(self, onnx_dir):
        from helper import load_text_to_speech
        return load_text_to_speech(onnx_dir, use_gpu=False)

    @property
    def voice_names(self):
        return sorted(self.voices.keys())

    def synthesize(self, text, lang="en", voice="M1", speed=1.05):
        style = self.voices.get(voice.upper(), self.voices.get("M1"))
        lang = lang.split("-")[0]
        if lang not in self.languages:
            lang = "en"

        t0 = time.perf_counter()
        for attempt in range(3):
            try:
                wav, dur = self.tts(text, lang, style, total_step=8, speed=1.05)
                break
            except Exception as e:
                if attempt < 2:
                    print(f"[WARN] GPU error (attempt {attempt+1}), retrying...")
                    time.sleep(0.5)
                else:
                    raise RuntimeError(f"GPU failed after 3 attempts: {e}")

        samples = wav[0, : int(self.tts.sample_rate * dur.item())]
        inference = time.perf_counter() - t0

        buf = io.BytesIO()
        sf.write(buf, samples, self.tts.sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue(), len(samples) / self.tts.sample_rate, inference


# --- WebSocket Server --- #
class WSTTSServer:
    def __init__(self, engine, port=8765):
        self.engine = engine
        self.port = port

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

                    loop = asyncio.get_event_loop()
                    audio_bytes, duration, latency = await loop.run_in_executor(
                        None, self.engine.synthesize,
                        text, req.get("lang", "en"), req.get("voice", "M1"), speed,
                    )

                    await websocket.send(json.dumps({
                        "type": "audio_meta",
                        "duration": round(duration, 3),
                        "latency_ms": round(latency * 1000),
                        "size": len(audio_bytes),
                    }))
                    await websocket.send(audio_bytes)

                except (asyncio.CancelledError, GeneratorExit):
                    break
                except Exception as e:
                    try:
                        await websocket.send(json.dumps({"type": "error", "message": str(e)}))
                    except Exception:
                        break
        except Exception:
            pass

    async def run(self):
        import websockets
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
