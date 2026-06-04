"""Speech-to-text for WhatsApp voice notes, via OpenAI Whisper.

Anthropic has no speech-to-text API, so Whisper is the one external vendor in the
otherwise Anthropic/Twilio/Blotato stack. It only turns Karen's audio into text;
Claude still writes every word of the actual post.

`transcribe()` takes the raw audio bytes (already downloaded from Twilio) and a
content-type, and returns a `Transcript` whose `outcome` the caller maps to a
WhatsApp reply. It never raises — every failure mode (silence, oversize, API
error, missing key, unsupported codec) resolves to a non-`ok` outcome so a bad
voice note can't crash the background task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.messaging.transcription")

# Whisper rejects files over 25 MB. A real WhatsApp voice note (OGG/Opus, ~16 kbps)
# would have to run for hours to approach this, so it doubles as an abuse guard.
_MAX_BYTES = 25 * 1024 * 1024

# Bias the decoder toward brand vocabulary so proper nouns transcribe correctly
# ("SIAL Paris", not "seal Paris"). Whisper treats this as a soft prompt.
_VOCAB_PROMPT = (
    "Globex International, SIAL Paris, Gulfood, Anuga, National Poultry Day, "
    "Chinese New Year, Ramadan, Memorial Day, Super Bowl, Alan's hot sauces, "
    "Karen Joseph, Blotato, Instagram, LinkedIn, poultry, shipment, trade show."
)

# Above this mean per-segment no-speech probability we treat the result as silence
# or a Whisper hallucination (it invents phrases like "Thanks for watching!" on
# quiet audio) and refuse to route it.
_NO_SPEECH_THRESHOLD = 0.6


class Outcome(StrEnum):
    ok = "ok"
    no_speech = "no_speech"  # empty, silent, or low-confidence — don't route it
    too_large = "too_large"  # over Whisper's 25 MB cap
    unavailable = "unavailable"  # no API key configured
    failed = "failed"  # API error, unsupported codec, anything unexpected


@dataclass(frozen=True)
class Transcript:
    outcome: Outcome
    text: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.ok


@lru_cache
def _client() -> AsyncOpenAI | None:
    key = get_settings().openai_api_key
    return AsyncOpenAI(api_key=key) if key else None


def _ext_for(content_type: str) -> str:
    """A filename extension Whisper recognizes, derived from the content-type."""
    subtype = content_type.split("/")[-1].split(";")[0].strip().removeprefix("x-")
    return subtype or "ogg"


def _no_speech(verbose: object) -> bool:
    """True when the transcript is empty or the segments read as silence."""
    text = (getattr(verbose, "text", "") or "").strip()
    if not text:
        return True
    segments = getattr(verbose, "segments", None) or []
    probs = [p for s in segments if (p := getattr(s, "no_speech_prob", None)) is not None]
    if probs and (sum(probs) / len(probs)) > _NO_SPEECH_THRESHOLD:
        return True
    return False


async def transcribe(audio_bytes: bytes, content_type: str) -> Transcript:
    """Transcribe a voice note. Never raises — failures become non-`ok` outcomes."""
    client = _client()
    if client is None:
        log.error("voice note received but OPENAI_API_KEY is not configured")
        return Transcript(Outcome.unavailable)

    if len(audio_bytes) > _MAX_BYTES:
        log.warning("voice note exceeds Whisper size cap", extra={"bytes": len(audio_bytes)})
        return Transcript(Outcome.too_large)

    settings = get_settings()
    filename = f"voice.{_ext_for(content_type)}"
    try:
        verbose = await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=(filename, audio_bytes, content_type),
            language="en",  # Karen instructs the bot in English; boosts short-clip accuracy
            prompt=_VOCAB_PROMPT,
            response_format="verbose_json",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — any STT failure must degrade, not crash
        log.error("whisper transcription failed", extra={"error": str(exc)})
        return Transcript(Outcome.failed)

    if _no_speech(verbose):
        log.info("voice note had no usable speech")
        return Transcript(Outcome.no_speech)

    text = (getattr(verbose, "text", "") or "").strip()
    log.info("voice note transcribed", extra={"chars": len(text)})
    return Transcript(Outcome.ok, text)
