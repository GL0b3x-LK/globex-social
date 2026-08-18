"""Outbound WhatsApp via Twilio. The twilio SDK is synchronous, so each send runs
in a thread to keep the FastAPI event loop responsive."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
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


# WhatsApp only allows free-form business messages inside 24 hours of the
# recipient's last inbound message. Outside it, a send is ACCEPTED (201 + SID)
# and then fails asynchronously with 63016 — so nothing raises, nothing retries,
# and the post is simply never seen. Only an approved template gets through.
SERVICE_WINDOW = timedelta(hours=24)

# Collapses anything WhatsApp forbids inside a template variable: newlines, tabs
# and runs of 4+ spaces are all rejected by Meta at send time.
_FORBIDDEN_IN_VARIABLE = re.compile(r"[\r\n\t]+|\s{4,}")


def flatten_variable(text: str, limit: int = 700) -> str:
    """Make a string safe to pass as a WhatsApp template variable."""
    flat = _FORBIDDEN_IN_VARIABLE.sub(" ", text or "").strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


async def within_window(to: str) -> bool:
    """Is the 24-hour service window open for this recipient?

    Unknown (they have never written) counts as closed: the expensive mistake is
    assuming it is open, because that failure is invisible.
    """
    last = await history.last_inbound_at(_whatsapp(to))
    if not last:
        return False
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when < SERVICE_WINDOW


def _send_template_sync(to: str, content_sid: str, variables: dict[str, str]) -> str:
    return (
        _client()
        .messages.create(
            from_=_sender(),
            to=_whatsapp(to),
            content_sid=content_sid,
            content_variables=json.dumps(variables),
        )
        .sid
    )


async def send_template(
    to: str,
    *,
    content_sid: str,
    variables: dict[str, str],
    body: str = "",
    media_url: str | None = None,
    post_id: str | None = None,
) -> str:
    """Send an approved WhatsApp template — the only thing that reaches a closed window.

    ``body``/``media_url`` are what the recipient effectively sees; they are
    recorded in the transcript so the conversation history reads the same
    whether a preview went out free-form or as a template.
    """
    sid = await asyncio.to_thread(_send_template_sync, to, content_sid, variables)
    log.info("sent template", extra={"to": to, "message_sid": sid, "content_sid": content_sid})
    await history.record_outbound(
        to, body=body, twilio_sid=sid, kind="preview", post_id=post_id, media_url=media_url
    )
    return sid


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


async def try_send_text(to: str, body: str, *, post_id: str | None = None) -> str | None:
    """Send, but never let a failed message abort the work that produced it.

    A courtesy line ("Updating the image… one sec") raising took an edit down
    with it: the acknowledgement is sent before the work starts, so an undelivered
    *status* message meant the requested change never happened at all. Delivery
    and work are separate concerns — the work finishes and is stored either way,
    and an undelivered preview can be re-sent from storage afterwards.
    """
    try:
        return await send_text(to, body, post_id=post_id)
    except Exception as exc:  # noqa: BLE001 — delivery is not the caller's job
        log.error("text delivery failed", extra={"to": to, "error": str(exc)[:200]})
        return None


async def try_send_media(
    to: str, body: str, media_url: str, *, post_id: str | None = None
) -> str | None:
    """``send_media`` that reports failure instead of raising. See ``try_send_text``."""
    try:
        return await send_media(to, body, media_url, post_id=post_id)
    except Exception as exc:  # noqa: BLE001 — delivery is not the caller's job
        log.error("media delivery failed", extra={"to": to, "error": str(exc)[:200]})
        return None


async def send_preview(
    to: str,
    body: str,
    media_url: str,
    *,
    identity: str,
    caption: str,
    post_id: str | None = None,
) -> str:
    """Deliver a rendered preview by whichever route WhatsApp currently allows.

    Inside the 24-hour window a free-form image + caption, exactly as before.
    Outside it — which is most mornings, since a scheduled draft lands on a
    thread nobody has written to since yesterday — the same picture and words
    go as the approved template, the only message WhatsApp will carry.

    ``identity`` names the post in one line ("74/156: Live at IPPE, out Tue 1am");
    ``caption`` is the post copy. Both are flattened, because Meta rejects a
    template variable containing a newline.
    """
    if await within_window(to):
        return await send_media(to, body, media_url, post_id=post_id)
    content_sid = get_settings().whatsapp_template_sid
    if not content_sid:
        raise RuntimeError(
            "the 24-hour window is closed and WHATSAPP_TEMPLATE_SID is unset; "
            "a free-form send would be accepted by Twilio and silently dropped"
        )
    log.info("window closed; sending the approved template", extra={"to": to})
    return await send_template(
        to,
        content_sid=content_sid,
        variables={
            "1": flatten_variable(identity, 220),
            "2": flatten_variable(caption),
            "3": media_url.rsplit("/", 1)[-1],
        },
        body=body,
        media_url=media_url,
        post_id=post_id,
    )


async def try_send_preview(
    to: str,
    body: str,
    media_url: str,
    *,
    identity: str,
    caption: str,
    post_id: str | None = None,
) -> str | None:
    """``send_preview`` that reports failure instead of raising."""
    try:
        return await send_preview(
            to, body, media_url, identity=identity, caption=caption, post_id=post_id
        )
    except Exception as exc:  # noqa: BLE001 — delivery is not the caller's job
        log.error("preview delivery failed", extra={"to": to, "error": str(exc)[:200]})
        return None
