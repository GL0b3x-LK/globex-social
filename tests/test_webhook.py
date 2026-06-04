"""Twilio webhook signature validation (always-on; no network/AI).

Uses Twilio's own RequestValidator to produce a correct signature, asserts the
endpoint accepts it and rejects a forged one. The validator's settings + the
background handler are patched so the test needs no .env and triggers no real work.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport
from twilio.request_validator import RequestValidator

from app.main import app
from app.workflows import on_demand

_AUTH = "test_auth_token_for_signing"
_URL = "https://example.com/webhooks/twilio/message"
_PARAMS = {
    "From": "whatsapp:+19170001111",
    "Body": "post about SIAL",
    "NumMedia": "0",
    "MessageSid": "SM1",
}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(
        "app.messaging.validator.get_settings",
        lambda: SimpleNamespace(twilio_auth_token=_AUTH, twilio_validate_signature=True),
    )
    handler = AsyncMock()
    monkeypatch.setattr(on_demand, "handle_incoming_message", handler)
    return handler


async def _post(signature: str) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        return await client.post(
            "/webhooks/twilio/message", data=_PARAMS, headers={"X-Twilio-Signature": signature}
        )


async def test_valid_signature_is_accepted(patched) -> None:
    signature = RequestValidator(_AUTH).compute_signature(_URL, _PARAMS)
    resp = await _post(signature)
    assert resp.status_code == 200
    assert "<Response>" in resp.text
    patched.assert_awaited_once()  # background processing was scheduled


async def test_forged_signature_is_rejected(patched) -> None:
    resp = await _post("clearly-not-a-valid-signature")
    assert resp.status_code == 403
    patched.assert_not_awaited()


async def test_media_is_forwarded_with_content_type(patched) -> None:
    # A voice note must reach the handler as (url, content_type) so it can be told
    # apart from a photo without downloading.
    params = {
        "From": "whatsapp:+19170001111",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://media.twiliocdn.test/v.ogg",
        "MediaContentType0": "audio/ogg",
        "MessageSid": "SM2",
    }
    signature = RequestValidator(_AUTH).compute_signature(_URL, params)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        resp = await client.post(
            "/webhooks/twilio/message", data=params, headers={"X-Twilio-Signature": signature}
        )
    assert resp.status_code == 200
    media_arg = patched.await_args.args[2]
    assert media_arg == [("https://media.twiliocdn.test/v.ogg", "audio/ogg")]
