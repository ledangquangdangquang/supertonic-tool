"""
Supertonic TTS WebSocket Server - Near-zero latency for Chrome extensions
Usage: uv run ws_server.py

Chrome extension connects once, sends text, receives audio instantly.
No HTTP overhead per request.
"""
import sys, os, io, json, asyncio, time
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import soundfile as sf
from helper import load_text_to_speech, load_voice_style, AVAILABLE_LANGS

# --- Load model --- #
ONNX_DIR = os.environ.get("ONNX_DIR", "../assets/onnx")
VOICE_DIR = os.environ.get("VOICE_DIR", "../assets/voice_styles")
API_KEY = os.environ.get("API_KEY", "sk-supertonic-local-key-2026")

print("Loading TTS model...")
tts = load_text_to_speech(ONNX_DIR, use_gpu=True)

VOICES = {}
for f in os.listdir(VOICE_DIR):
    if f.endswith(".json"):
        VOICES[f.replace(".json", "")] = load_voice_style([os.path.join(VOICE_DIR, f)])

# Warmup
tts("warmup", "en", VOICES["M1"], 8, 1.05)
tts("warmup2", "en", VOICES["M1"], 8, 1.05)
print(f"Ready! Voices: {list(VOICES.keys())}")


def synthesize(text, lang="en", voice="M1", speed=1.05):
    style = VOICES.get(voice.upper(), VOICES["M1"])
    lang = lang.split("-")[0]
    if lang not in AVAILABLE_LANGS:
        lang = "en"

    start = time.perf_counter()
    wav, dur = tts(text, lang, style, total_step=8, speed=speed)
    elapsed = time.perf_counter() - start

    samples = wav[0, :int(tts.sample_rate * dur.item())]
    buf = io.BytesIO()
    sf.write(buf, samples, tts.sample_rate, format="WAV", subtype="PCM_16")

    return buf.getvalue(), dur.item(), elapsed


async def handle_client(websocket):
    """Handle a single WebSocket connection."""
    # Auth: first message must be the API key
    try:
        auth_msg = await asyncio.wait_for(websocket.recv(), timeout=5)
        if auth_msg != API_KEY:
            await websocket.close(4001, "Invalid API key")
            return
        await websocket.send(json.dumps({"status": "connected", "voices": list(VOICES.keys())}))
    except Exception:
        return

    # Main loop: receive text, send audio
    async for message in websocket:
        try:
            req = json.loads(message)
            text = req.get("text", "").strip()
            if not text:
                continue

            lang = req.get("lang", "en")
            voice = req.get("voice", "M1")
            speed = req.get("speed", 1.05)

            # Run synthesis directly (no thread overhead)
            audio_bytes, duration, elapsed = synthesize(text, lang, voice, speed)

            # Send metadata then binary audio
            await websocket.send(json.dumps({
                "type": "audio_meta",
                "duration": round(duration, 3),
                "latency_ms": round(elapsed * 1000),
                "size": len(audio_bytes),
            }))
            await websocket.send(audio_bytes)

        except Exception as e:
            await websocket.send(json.dumps({"type": "error", "message": str(e)}))


async def main():
    import websockets
    port = int(os.environ.get("WS_PORT", 8765))
    async with websockets.serve(handle_client, "127.0.0.1", port):
        print(f"WebSocket server on ws://127.0.0.1:{port}")
        print("Chrome extension can connect now!")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
