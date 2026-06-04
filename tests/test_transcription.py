"""Whisper transcription tests — fully offline (the OpenAI client is faked).

Covers every outcome the handler branches on: a clean transcript, silence/empty,
the hallucination guard (high no_speech_prob), the size cap, a missing key, and an
API error. None of these may raise — each must resolve to a `Transcript` outcome.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.messaging import transcription
from app.messaging.transcription import Outcome


def _fake_client(*, create=None, raises=None):
    async def _create(**kwargs):
        if raises is not None:
            raise raises
        return create

    return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=_create)))


def _verbose(text: str, no_speech_probs: list[float]):
    segments = [SimpleNamespace(no_speech_prob=p) for p in no_speech_probs]
    return SimpleNamespace(text=text, segments=segments)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    # transcribe() reads settings.whisper_model; keep it off the real .env.
    monkeypatch.setattr(
        transcription,
        "get_settings",
        lambda: SimpleNamespace(whisper_model="whisper-1", openai_api_key="sk-test"),
    )


async def test_clean_transcript_is_ok(monkeypatch) -> None:
    verbose = _verbose("Post about us at SIAL Paris.", [0.05, 0.02])
    monkeypatch.setattr(transcription, "_client", lambda: _fake_client(create=verbose))
    result = await transcription.transcribe(b"audio", "audio/ogg")
    assert result.outcome is Outcome.ok
    assert result.ok
    assert result.text == "Post about us at SIAL Paris."


async def test_empty_text_is_no_speech(monkeypatch) -> None:
    monkeypatch.setattr(
        transcription, "_client", lambda: _fake_client(create=_verbose("   ", [0.1]))
    )
    result = await transcription.transcribe(b"audio", "audio/ogg")
    assert result.outcome is Outcome.no_speech
    assert not result.ok


async def test_high_no_speech_prob_is_hallucination_guarded(monkeypatch) -> None:
    # Whisper invents "Thanks for watching!" on silence, but flags it with high prob.
    verbose = _verbose("Thanks for watching!", [0.92, 0.88])
    monkeypatch.setattr(transcription, "_client", lambda: _fake_client(create=verbose))
    result = await transcription.transcribe(b"audio", "audio/ogg")
    assert result.outcome is Outcome.no_speech


async def test_oversize_audio_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(transcription, "_MAX_BYTES", 8)
    monkeypatch.setattr(
        transcription, "_client", lambda: _fake_client(create=_verbose("hi", [0.1]))
    )
    result = await transcription.transcribe(b"x" * 9, "audio/ogg")
    assert result.outcome is Outcome.too_large


async def test_missing_key_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(transcription, "_client", lambda: None)
    result = await transcription.transcribe(b"audio", "audio/ogg")
    assert result.outcome is Outcome.unavailable


async def test_api_error_degrades_to_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        transcription, "_client", lambda: _fake_client(raises=RuntimeError("whisper down"))
    )
    result = await transcription.transcribe(b"audio", "audio/ogg")
    assert result.outcome is Outcome.failed


def test_ext_for_maps_content_types() -> None:
    assert transcription._ext_for("audio/ogg") == "ogg"
    assert transcription._ext_for("audio/x-m4a") == "m4a"
    assert transcription._ext_for("audio/mpeg; codecs=mp3") == "mpeg"
    assert transcription._ext_for("audio/") == "ogg"
