"""
Supertonic TTS API Server - Google Cloud TTS-style REST API
Usage: uv run api_server.py
"""

import base64
import io
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader, APIKeyQuery
from pydantic import BaseModel, Field
from enum import Enum
from helper import load_text_to_speech, load_voice_style, AVAILABLE_LANGS

# --- API Key Config --- #
API_KEY = os.environ.get("API_KEY", "sk-supertonic-local-key-2026")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key_query = APIKeyQuery(name="key", auto_error=False)


def verify_api_key(
    header_key: str = Security(_api_key_header),
    query_key: str = Security(_api_key_query),
):
    key = header_key or query_key
    if not key or key != API_KEY:
        raise HTTPException(401, "Invalid or missing API key")
    return key

# --- Load model at startup --- #
ONNX_DIR = os.environ.get("ONNX_DIR", "../assets/onnx")
VOICE_DIR = os.environ.get("VOICE_DIR", "../assets/voice_styles")
USE_GPU = os.environ.get("USE_GPU", "1") != "0"

print("Loading TTS model...")
tts = load_text_to_speech(ONNX_DIR, use_gpu=USE_GPU)

# Preload all voice styles
VOICES = {}
for f in os.listdir(VOICE_DIR):
    if f.endswith(".json"):
        name = f.replace(".json", "")
        VOICES[name] = load_voice_style([os.path.join(VOICE_DIR, f)])
print(f"Loaded voices: {list(VOICES.keys())}")

# Warmup inference
print("Warming up GPU...")
_style = VOICES.get("M1", list(VOICES.values())[0])
tts("warmup", "en", _style, total_step=8, speed=1.05)
print("Ready!")


# --- API Models --- #
class AudioEncoding(str, Enum):
    LINEAR16 = "LINEAR16"
    MP3 = "MP3"


class VoiceGender(str, Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"


class AudioConfig(BaseModel):
    audioEncoding: AudioEncoding = AudioEncoding.LINEAR16
    speakingRate: float = Field(default=1.05, ge=0.25, le=4.0)


class VoiceSelection(BaseModel):
    languageCode: str = "en"
    name: str = "M1"


class SynthesisInput(BaseModel):
    text: str


class SynthesizeRequest(BaseModel):
    input: SynthesisInput
    voice: VoiceSelection = VoiceSelection()
    audioConfig: AudioConfig = AudioConfig()


class SynthesizeResponse(BaseModel):
    audioContent: str  # base64 encoded
    durationSeconds: float


# --- App --- #
app = FastAPI(title="Supertonic TTS API", version="1.0.0")


def _synthesize_core(text, lang, style, speed):
    wav, dur = tts(text, lang, style, total_step=8, speed=speed)
    samples = wav[0, :int(tts.sample_rate * dur.item())]
    buf = io.BytesIO()
    sf.write(buf, samples, tts.sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue(), dur.item()


@app.get("/v1/voices")
def list_voices():
    """List available voices and languages."""
    voices = []
    for name in sorted(VOICES.keys()):
        gender = "MALE" if name.startswith("M") else "FEMALE"
        voices.append({
            "name": name,
            "gender": gender,
            "languageCodes": AVAILABLE_LANGS,
        })
    return {"voices": voices}


@app.post("/v1/text:synthesize", response_model=SynthesizeResponse, dependencies=[Depends(verify_api_key)])
def synthesize(req: SynthesizeRequest):
    """Synthesize speech from text (Google Cloud TTS compatible)."""
    voice_name = req.voice.name.upper()
    if voice_name not in VOICES:
        raise HTTPException(400, f"Voice '{voice_name}' not found. Available: {list(VOICES.keys())}")

    lang = req.voice.languageCode.lower().split("-")[0]
    if lang not in AVAILABLE_LANGS:
        raise HTTPException(400, f"Language '{lang}' not supported. Available: {AVAILABLE_LANGS}")

    text = req.input.text.strip()
    if not text:
        raise HTTPException(400, "Text cannot be empty")

    style = VOICES[voice_name]
    audio_bytes, duration = _synthesize_core(text, lang, style, req.audioConfig.speakingRate)
    audio_b64 = base64.b64encode(audio_bytes).decode()
    return SynthesizeResponse(audioContent=audio_b64, durationSeconds=duration)


@app.post("/v1/text:synthesize/stream", dependencies=[Depends(verify_api_key)])
def synthesize_stream(req: SynthesizeRequest):
    """Synthesize and return audio file directly (streaming)."""
    voice_name = req.voice.name.upper()
    if voice_name not in VOICES:
        raise HTTPException(400, f"Voice '{voice_name}' not found.")

    lang = req.voice.languageCode.lower().split("-")[0]
    if lang not in AVAILABLE_LANGS:
        raise HTTPException(400, f"Language '{lang}' not supported.")

    text = req.input.text.strip()
    if not text:
        raise HTTPException(400, "Text cannot be empty")

    style = VOICES[voice_name]
    audio_bytes, duration = _synthesize_core(text, lang, style, req.audioConfig.speakingRate)
    return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/wav", headers={
        "X-Audio-Duration": f"{duration:.2f}"
    })


# --- Google Cloud TTS Compatible Endpoint --- #
# Maps Google voice names (e.g. "vi-VN-Standard-A") to local voices
GENDER_MAP = {"MALE": ["M1","M2","M3","M4","M5"], "FEMALE": ["F1","F2","F3","F4","F5"]}

def _map_google_voice(voice_obj: dict) -> tuple[str, str]:
    """Map Google Cloud TTS voice format to local voice."""
    lang_code = voice_obj.get("languageCode", "en-US").split("-")[0]
    name = voice_obj.get("name", "")
    gender = voice_obj.get("ssmlGender", "FEMALE")

    # If name matches our format directly (M1, F1, etc)
    if name.upper() in VOICES:
        return name.upper(), lang_code

    # Map Google gender to local voice
    voices = GENDER_MAP.get(gender, GENDER_MAP["FEMALE"])
    # Use voice variant from Google name (e.g. "vi-VN-Standard-A" -> A=index 0)
    idx = 0
    if name and name[-1].isalpha():
        idx = min(ord(name[-1].upper()) - ord('A'), len(voices) - 1)
    return voices[idx], lang_code


@app.post("/v1beta1/text:synthesize", dependencies=[Depends(verify_api_key)])
@app.post("/v1/text:synthesize/google", dependencies=[Depends(verify_api_key)])
def synthesize_google_compat(request: dict):
    """
    100% Google Cloud TTS compatible endpoint.
    Just change URL from https://texttospeech.googleapis.com to http://localhost:8000
    """
    # Parse Google format
    input_obj = request.get("input", {})
    text = input_obj.get("text", "") or input_obj.get("ssml", "")
    voice_obj = request.get("voice", {})
    audio_config = request.get("audioConfig", {})

    if not text:
        raise HTTPException(400, "No text provided")

    voice_name, lang = _map_google_voice(voice_obj)
    if lang not in AVAILABLE_LANGS:
        raise HTTPException(400, f"Language '{lang}' not supported")

    style = VOICES.get(voice_name, VOICES["M1"])
    speed = audio_config.get("speakingRate", 1.05)
    speed = max(0.25, min(4.0, speed))

    audio_bytes, duration = _synthesize_core(text, lang, style, speed)
    audio_b64 = base64.b64encode(audio_bytes).decode()

    # Return exact Google format
    return {"audioContent": audio_b64}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
