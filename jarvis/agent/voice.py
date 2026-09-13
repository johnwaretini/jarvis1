"""voice.py — ElevenLabs speech, both directions. Server-side only.

The API key lives here and never reaches the browser. The page posts text to
/api/speak (gets mp3 bytes back) and audio to /api/listen (gets a transcript).
Nothing sensitive shows up in devtools or a screen recording.

Speech OUT: ElevenLabs text-to-speech.
Speech IN:  ElevenLabs Scribe  (/v1/speech-to-text, model scribe_v1).

Paid API: this runs only when ELEVENLABS_API_KEY is set. Setting the key is the
opt-in — the "never spend without asking" guardrail holds because nothing calls
a paid endpoint until you deliberately provide the key. With no key, both
directions degrade loudly so the UI can say exactly what's missing (a blocked
or unconfigured mic is the most confusing failure in this whole build).
"""
from __future__ import annotations

import os
import uuid
import urllib.request

TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5")
STT_MODEL = "scribe_v1"
# Default voice: ElevenLabs "Rachel" (a public preset). Override with your own.
DEFAULT_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")


def _key() -> str:
    return os.environ.get("ELEVENLABS_API_KEY", "").strip()


def available() -> dict:
    if _key():
        return {"available": True, "reason": "ElevenLabs key set"}
    return {"available": False,
            "reason": "no ELEVENLABS_API_KEY — voice is off; text still works"}


def speak(text: str) -> tuple[bytes, str | None]:
    """Text -> mp3 bytes. Returns (audio, error)."""
    text = (text or "").strip()
    if not text:
        return b"", "nothing to speak"
    key = _key()
    if not key:
        return b"", ("voice unavailable: set ELEVENLABS_API_KEY on the server "
                     "(the page never sees it)")
    voice = DEFAULT_VOICE
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    import json
    body = json.dumps({
        "text": text,
        "model_id": TTS_MODEL,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "xi-api-key": key,
        "content-type": "application/json",
        "accept": "audio/mpeg",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return b"", f"ElevenLabs TTS {e.code}: {detail}"
    except Exception as e:
        return b"", f"ElevenLabs TTS unreachable ({type(e).__name__})"


def listen(audio: bytes, content_type: str) -> tuple[str, str | None]:
    """Recorded audio bytes -> transcript. Returns (text, error).

    Uses ElevenLabs Scribe. The browser records with MediaRecorder (works in
    every browser, unlike Web Speech) and posts the blob here; we forward it as
    multipart to Scribe."""
    if not audio:
        return "", "no audio received (is the mic blocked?)"
    key = _key()
    if not key:
        return "", ("transcription unavailable: set ELEVENLABS_API_KEY on the "
                    "server (the page never sees it)")
    ext = "webm"
    if "ogg" in content_type: ext = "ogg"
    elif "mp4" in content_type or "m4a" in content_type: ext = "mp4"
    elif "wav" in content_type: ext = "wav"
    body, boundary = _multipart({"model_id": STT_MODEL},
                                {"file": (f"audio.{ext}", audio,
                                          content_type or "application/octet-stream")})
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/speech-to-text", data=body, headers={
            "xi-api-key": key,
            "content-type": f"multipart/form-data; boundary={boundary}",
        })
    try:
        import json
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read())
        return payload.get("text", ""), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return "", f"ElevenLabs Scribe {e.code}: {detail}"
    except Exception as e:
        return "", f"ElevenLabs Scribe unreachable ({type(e).__name__})"


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Build a multipart/form-data body with stdlib only."""
    boundary = "----jarvis" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    for name, (filename, content, ctype) in files.items():
        out += f"--{boundary}\r\n".encode()
        out += (f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n').encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += content
        out += b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), boundary
