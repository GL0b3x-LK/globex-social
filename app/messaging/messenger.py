"""Transport-blind outbound messaging: dispatch on the address prefix.

Every address in the system — AUTHORIZED_NUMBERS, APPROVAL_RECIPIENTS, the
phone_number columns — is an opaque string that names its own transport:

    whatsapp:+19178592787   → Twilio  (24h window, templates, async failures)
    telegram:847213905      → Bot API (none of that; a group id is negative)

The workflows call these six functions and never learn which app a message
left through, which is what lets one operator live on WhatsApp and another on
Telegram with identical behaviour. Dispatch resolves the target module's
attribute at CALL time, so tests that patch twilio_client.send_text keep
intercepting sends exactly as before.
"""

from __future__ import annotations

from types import ModuleType

from app.logging_config import get_logger
from app.messaging import telegram_client, twilio_client

log = get_logger("app.messaging.messenger")


def _transport(to: str) -> ModuleType:
    return telegram_client if to.strip().lower().startswith("telegram:") else twilio_client


async def send_text(to: str, body: str, *, post_id: str | None = None) -> str:
    return await _transport(to).send_text(to, body, post_id=post_id)


async def send_media(to: str, body: str, media_url: str, *, post_id: str | None = None) -> str:
    return await _transport(to).send_media(to, body, media_url, post_id=post_id)


async def send_preview(
    to: str,
    body: str,
    media_url: str,
    *,
    identity: str,
    caption: str,
    post_id: str | None = None,
) -> str:
    return await _transport(to).send_preview(
        to, body, media_url, identity=identity, caption=caption, post_id=post_id
    )


# The try_ variants catch HERE, above dispatch, rather than delegating to each
# transport's own wrapper: one place implements "delivery failure must never
# abort the work that produced the message" for every transport, and a patched
# messenger.send_* in a test is reached by messenger.try_send_* exactly as the
# real path would be.


async def try_send_text(to: str, body: str, *, post_id: str | None = None) -> str | None:
    try:
        return await send_text(to, body, post_id=post_id)
    except Exception as exc:  # noqa: BLE001 — delivery is not the caller's job
        log.error("text delivery failed", extra={"to": to, "error": str(exc)[:200]})
        return None


async def try_send_media(
    to: str, body: str, media_url: str, *, post_id: str | None = None
) -> str | None:
    try:
        return await send_media(to, body, media_url, post_id=post_id)
    except Exception as exc:  # noqa: BLE001
        log.error("media delivery failed", extra={"to": to, "error": str(exc)[:200]})
        return None


async def try_send_preview(
    to: str,
    body: str,
    media_url: str,
    *,
    identity: str,
    caption: str,
    post_id: str | None = None,
) -> str | None:
    try:
        return await send_preview(
            to, body, media_url, identity=identity, caption=caption, post_id=post_id
        )
    except Exception as exc:  # noqa: BLE001
        log.error("preview delivery failed", extra={"to": to, "error": str(exc)[:200]})
        return None
