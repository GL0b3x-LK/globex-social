"""Outbound Telegram via the Bot API. Mirrors twilio_client's public surface
(send_text / send_media / send_preview and their try_ variants) so messenger.py
can dispatch on the address prefix without the workflows knowing which app a
message leaves through.

Telegram is the simpler transport in every way that has hurt us on WhatsApp:
there is no 24-hour service window, no message templates, no category review,
and a send fails HERE, synchronously, with a real description — never a 201
followed by a silent out-of-band failure. So there is no within_window, no
send_template, and nothing for the status callback to catch.

Addresses look like "telegram:<chat_id>" (a group id is negative). Message ids
are only unique per chat, so the transcript stores them as "tg:<chat>:<id>" —
the same key the webhook builds from a swipe-reply, which is what keeps
reply-to-post resolution working across both transports.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_config import get_logger
from app.messaging import history

log = get_logger("app.messaging.telegram")

# Bot API hard limits: message text 4096 chars, photo caption 1024. A body over
# the caption cap is sent as photo-then-text rather than truncated — the copy
# being approved must never be silently cut.
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


def _token() -> str:
    token = get_settings().telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is unset; cannot send via Telegram")
    return token


def _chat_id(to: str) -> str:
    """ "telegram:-1001234" -> "-1001234". Accepts a bare id for robustness."""
    return to.removeprefix("telegram:").strip()


def _sid(chat_id: str, message_id: object) -> str:
    return f"tg:{chat_id}:{message_id}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _api(method: str, payload: dict[str, object], *, timeout: float = 15.0) -> dict:
    """Call one Bot API method; raise with Telegram's own description on failure.

    Telegram wraps everything in {"ok": bool, ...} and puts the human-readable
    reason in "description" — surfacing it verbatim is what makes a failed send
    debuggable from a single log line (unlike a bare Twilio error code).
    """
    url = f"https://api.telegram.org/bot{_token()}/{method}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method} failed: {data.get('description', resp.text)[:200]}")
    return data["result"]


async def send_text(to: str, body: str, *, post_id: str | None = None) -> str:
    chat = _chat_id(to)
    result = await _api("sendMessage", {"chat_id": chat, "text": _clip(body, TEXT_LIMIT)})
    sid = _sid(chat, result.get("message_id"))
    log.info("sent text", extra={"to": to, "message_sid": sid})
    await history.record_outbound(to, body=body, twilio_sid=sid, kind="text", post_id=post_id)
    return sid


async def send_media(to: str, body: str, media_url: str, *, post_id: str | None = None) -> str:
    """Send an image preview with a caption.

    A caption over Telegram's 1024-char cap goes as two messages — the photo
    (uncaptioned) then the full text — so the recipient always reads the exact
    copy that would publish. The photo's sid is the one recorded against the
    post: swipe-replying to the IMAGE is what re-opens the post.
    """
    chat = _chat_id(to)
    if len(body) <= CAPTION_LIMIT:
        result = await _api("sendPhoto", {"chat_id": chat, "photo": media_url, "caption": body})
    else:
        result = await _api("sendPhoto", {"chat_id": chat, "photo": media_url})
        await _api("sendMessage", {"chat_id": chat, "text": _clip(body, TEXT_LIMIT)})
    sid = _sid(chat, result.get("message_id"))
    log.info("sent media", extra={"to": to, "message_sid": sid})
    await history.record_outbound(
        to, body=body, twilio_sid=sid, kind="preview", post_id=post_id, media_url=media_url
    )
    return sid


async def send_preview(
    to: str,
    body: str,
    media_url: str,
    *,
    identity: str,
    caption: str,
    post_id: str | None = None,
) -> str:
    """Deliver a rendered preview. On Telegram there is no closed-window route:
    every preview is the same free-form image + caption, every day. `identity`
    and `caption` exist only to match the WhatsApp signature — `body` already
    carries both.
    """
    del identity, caption  # WhatsApp-template concerns; no equivalent here
    return await send_media(to, body, media_url, post_id=post_id)


async def try_send_text(to: str, body: str, *, post_id: str | None = None) -> str | None:
    """Send, but never let a failed message abort the work that produced it."""
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


async def download_file(file_id: str, *, timeout: float = 10.0) -> tuple[bytes, str]:
    """Fetch a file a user attached (photo/voice/video) by its file_id.

    Two hops: getFile resolves the id to a short-lived path, then the file
    endpoint serves the bytes. The download URL embeds the bot token, which is
    why the transcript stores the opaque "telegram-file:<id>" pseudo-URL and
    resolution happens only here, at download time — a token must never land in
    the database or a log line.
    """
    info = await _api("getFile", {"file_id": file_id}, timeout=timeout)
    path = info.get("file_path")
    if not path:
        raise RuntimeError(f"telegram getFile returned no path for {file_id}")
    url = f"https://api.telegram.org/file/bot{_token()}/{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    content_type = resp.headers.get("content-type", "application/octet-stream")
    log.info("downloaded media", extra={"bytes": len(resp.content), "content_type": content_type})
    return resp.content, content_type
