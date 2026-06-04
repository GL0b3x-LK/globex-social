"""Async wrappers over the sync `messages` DB helpers.

The transcript log is best-effort: a logging failure must never break sending or
receiving a message, so `record_*` swallow errors (the conversation still works,
it just won't remember that one line).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.db import messages as messages_db
from app.logging_config import get_logger

log = get_logger("app.messaging.history")
Row = dict[str, Any]


async def record_inbound(
    phone: str,
    *,
    body: str | None,
    twilio_sid: str | None = None,
    media_url: str | None = None,
    kind: str = "text",
) -> None:
    try:
        await asyncio.to_thread(
            messages_db.create,
            phone_number=phone,
            role="karen",
            body=body,
            twilio_sid=twilio_sid,
            media_url=media_url,
            kind=kind,
        )
    except Exception as exc:  # noqa: BLE001 — logging must not break the flow
        log.error("failed to log inbound message", extra={"error": str(exc)})


async def record_outbound(
    phone: str,
    *,
    body: str | None,
    twilio_sid: str | None = None,
    kind: str = "text",
    post_id: str | None = None,
    media_url: str | None = None,
) -> None:
    try:
        await asyncio.to_thread(
            messages_db.create,
            phone_number=phone,
            role="agent",
            body=body,
            twilio_sid=twilio_sid,
            media_url=media_url,
            kind=kind,
            post_id=post_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("failed to log outbound message", extra={"error": str(exc)})


async def recent(phone: str, limit: int = 25) -> list[Row]:
    return await asyncio.to_thread(messages_db.recent, phone, limit)


async def by_sid(twilio_sid: str) -> Row | None:
    return await asyncio.to_thread(messages_db.by_sid, twilio_sid)


async def count(phone: str) -> int:
    return await asyncio.to_thread(messages_db.count, phone)


async def page(phone: str, offset: int, limit: int) -> list[Row]:
    return await asyncio.to_thread(messages_db.page, phone, offset, limit)
