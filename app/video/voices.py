"""ElevenLabs voices — one locked voice per character.

A character's voice is created ONCE and its id is stored on the roster, exactly
like the reference images: re-designing a voice per video would give a different
person each time, and the client explicitly rejected accent drift mid-video.

Two steps, per the Voice Design API:
  1. POST /v1/text-to-voice/design  -> N previews, each with a generated_voice_id
  2. POST /v1/text-to-voice         -> saves one preview as a permanent voice_id

Speech generation uses the timestamped endpoint so caption timing comes free
with the audio rather than needing a separate transcription pass.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.video.voices")

BASE_URL = "https://api.elevenlabs.io/v1"
_TIMEOUT_S = 120.0

# Every character speaks neutral American English (see characters.json _meta
# voice_rule). Appended to each character's voice_direction so the accent can
# never be inferred from the persona's appearance.
ACCENT = "Neutral American English accent, standard US pronunciation."

# Preview text: fixed rather than auto-generated, so preview cost is predictable
# and every character is auditioned saying the same on-brand line. Must be
# 100-1000 characters, and must pass our own forbidden-claims linter.
PREVIEW_TEXT = (
    "Every carton that leaves this floor is checked, sealed and logged before it ships. "
    "That is how we keep the same standard from our plant to your market, every single week."
)


@dataclass(frozen=True)
class VoicePreview:
    generated_voice_id: str
    audio: bytes
    duration_secs: float


class VoiceError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    key = get_settings().elevenlabs_api_key
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not configured")
    return {"xi-api-key": key, "Content-Type": "application/json"}


def voice_description(character_direction: str, role: str) -> str:
    """The design prompt: the persona's own direction plus the mandatory accent."""
    return f"{character_direction} {ACCENT} Speaks as a {role.lower()} in a food company."


def design(description: str, *, text: str = PREVIEW_TEXT) -> list[VoicePreview]:
    """Generate candidate voices for a description. Does not persist anything."""
    body: dict[str, Any] = {"voice_description": description, "text": text}
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT_S) as client:
        resp = client.post("/text-to-voice/design", headers=_headers(), json=body)
    if resp.status_code >= 400:
        raise VoiceError(f"design failed {resp.status_code}: {resp.text[:300]}")
    previews = (resp.json() or {}).get("previews") or []
    return [
        VoicePreview(
            generated_voice_id=p["generated_voice_id"],
            audio=base64.b64decode(p["audio_base_64"]),
            duration_secs=float(p.get("duration_secs") or 0.0),
        )
        for p in previews
    ]


def save(name: str, description: str, generated_voice_id: str, labels: dict[str, str]) -> str:
    """Persist a preview as a permanent voice; returns the durable voice_id."""
    body = {
        "voice_name": name,
        "voice_description": description,
        "generated_voice_id": generated_voice_id,
        "labels": labels,
    }
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT_S) as client:
        resp = client.post("/text-to-voice", headers=_headers(), json=body)
    if resp.status_code >= 400:
        raise VoiceError(f"save failed {resp.status_code}: {resp.text[:300]}")
    voice_id = (resp.json() or {}).get("voice_id")
    if not voice_id:
        raise VoiceError("no voice_id returned")
    return str(voice_id)


def speak(
    voice_id: str, text: str, *, model_id: str = "eleven_multilingual_v2"
) -> tuple[bytes, list[dict[str, Any]]]:
    """Speak `text` in a locked voice. Returns (audio_bytes, word_timings).

    Uses the with-timestamps endpoint: ElevenLabs returns character-level
    alignment, which is grouped into word timings here so captions can be timed
    without a second transcription pass.
    """
    body = {"text": text, "model_id": model_id}
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT_S) as client:
        resp = client.post(
            f"/text-to-speech/{voice_id}/with-timestamps", headers=_headers(), json=body
        )
    if resp.status_code >= 400:
        raise VoiceError(f"tts failed {resp.status_code}: {resp.text[:300]}")
    data = resp.json() or {}
    audio = base64.b64decode(data.get("audio_base64") or "")
    return audio, words_from_alignment(data.get("alignment") or {})


def words_from_alignment(alignment: dict[str, Any]) -> list[dict[str, Any]]:
    """Group character-level alignment into word timings for caption rendering."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict[str, Any]] = []
    current, start = "", None
    for i, ch in enumerate(chars):
        if ch.isspace():
            if current:
                words.append({"word": current, "start": start, "end": ends[i - 1]})
                current, start = "", None
            continue
        if not current:
            start = starts[i]
        current += ch
    if current and start is not None:
        words.append({"word": current, "start": start, "end": ends[len(chars) - 1]})
    return words
