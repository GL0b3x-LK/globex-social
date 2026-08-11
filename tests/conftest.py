"""Shared pytest fixtures.

DB helper tests are integration tests against the dev Supabase project. If
Supabase isn't configured or reachable (e.g. CI without secrets, or schema not
yet applied), they skip rather than fail.
"""

from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.db.client import ping


@pytest.fixture(autouse=True)
def _no_real_whatsapp_sends(monkeypatch):
    """No test may reach api.twilio.com.

    Workflow tests stub the sends they assert on, but any *other* send on the
    path — the learning layer's follow-up question, a status line — ran for
    real: billed messages to the placeholder number tests use, which Twilio
    rejected as invalid (error 21211). Patching the two synchronous SDK calls
    closes every route out, including ones a future test forgets, while leaving
    ``send_text``/``send_media`` themselves real so their logging and history
    recording still get exercised.
    """
    from app.messaging import history, twilio_client

    async def _no_history(*_a, **_kw):
        return None

    monkeypatch.setattr(twilio_client, "_send_text_sync", lambda to, body: "SMtest")
    monkeypatch.setattr(twilio_client, "_send_media_sync", lambda to, body, media_url: "MMtest")
    monkeypatch.setattr(history, "record_outbound", _no_history)
    yield


@pytest.fixture(autouse=True)
def _no_learned_rules_over_the_network(monkeypatch):
    """Generation now reads the learned-rules store; unit tests must not reach
    Supabase for it. Tests OF the store (test_learning) re-patch these
    per-test, and their later patch wins."""
    from app.db import storage

    monkeypatch.setattr(storage, "read_bytes", lambda path, **kw: None)
    yield


@pytest.fixture(scope="session")
def supabase_ready() -> bool:
    try:
        get_settings()
        ping()
    except Exception as exc:  # noqa: BLE001 — surface any config/connectivity issue as a skip
        pytest.skip(f"Supabase not configured/reachable (or schema not applied): {exc}")
    return True


@pytest.fixture(scope="session")
def anthropic_live() -> bool:
    """Gate for live Claude API tests (real calls cost money + are non-deterministic).

    Skips unless RUN_AI_LIVE is set AND an API key is configured. Run with:
        RUN_AI_LIVE=1 .venv\\Scripts\\python.exe -m pytest tests/test_generator.py tests/test_intent.py
    """
    if not os.environ.get("RUN_AI_LIVE"):
        pytest.skip("live AI tests disabled (set RUN_AI_LIVE=1 to enable)")
    try:
        get_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings not configured: {exc}")
    return True
