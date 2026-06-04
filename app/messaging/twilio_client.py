"""Outbound WhatsApp via Twilio. The twilio SDK is synchronous, so each send runs
in a thread to keep the FastAPI event loop responsive."""

from __future__ import annotations

import asyncio
from functools import lru_cache

from twilio.rest import Client

from app.config import get_settings
from app.logging_config import get_logger
from app.messaging import history

log = get_logger("app.messaging.twilio")


@lru_cache
def _client() -> Client:
    settings = get_settings()
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _whatsapp(addr: str) -> str:
    return addr if addr.startswith("whatsapp:") else f"whatsapp:{addr}"


def _sender() -> str:
    return _whatsapp(get_settings().twilio_whatsapp_number)


def _send_text_sync(to: str, body: str) -> str:
    return _client().messages.create(from_=_sender(), to=_whatsapp(to), body=body).sid


def _send_media_sync(to: str, body: str, media_url: str) -> str:
    return (
        _client()
        .messages.create(from_=_sender(), to=_whatsapp(to), body=body, media_url=[media_url])
        .sid
    )


async def send_text(to: str, body: str, *, post_id: str | None = None) -> str:
    sid = await asyncio.to_thread(_send_text_sync, to, body)
    log.info("sent text", extra={"to": to, "message_sid": sid})
    await history.record_outbound(to, body=body, twilio_sid=sid, kind="text", post_id=post_id)
    return sid


async def send_media(to: str, body: str, media_url: str, *, post_id: str | None = None) -> str:
    """Send an image preview (the rendered post) with a caption."""
    sid = await asyncio.to_thread(_send_media_sync, to, body, media_url)
    log.info("sent media", extra={"to": to, "message_sid": sid})
    await history.record_outbound(
        to, body=body, twilio_sid=sid, kind="preview", post_id=post_id, media_url=media_url
    )
    return sid
