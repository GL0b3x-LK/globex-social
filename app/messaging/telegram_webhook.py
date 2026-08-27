"""Telegram Bot API webhook: normalise an Update into the same
handle_incoming_message(...) call the Twilio webhook makes, so everything
downstream — intent, state machine, approval flow, memory — is transport-blind.

Authentication is Telegram's secret_token: every webhook call carries the
X-Telegram-Bot-Api-Secret-Token header with the value we registered in
setWebhook (scripts/setup_telegram.py). Constant-time compare, 403 otherwise.

Onboarding is deliberate: a message from a chat NOT on the allowlist gets a
one-line reply naming its chat id. Telegram has no phone-number directory — a
chat id is only learnable after someone writes to the bot — so without this
reply a new operator's first experience is silence, which is exactly the
failure mode that burned us on WhatsApp. The reply reveals nothing sensitive.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import get_logger
from app.messaging import telegram_client
from app.workflows import on_demand

log = get_logger("app.messaging.telegram_webhook")

router = APIRouter(prefix="/webhooks/telegram", tags=["telegram"])

_UNKNOWN_CHAT_REPLY = (
    "This chat isn't connected to the Globex system yet.\n"
    "Chat ID: telegram:{chat_id}\n"
    "Send that line to Abdul to get access."
)


def _verify(request: Request) -> None:
    expected = get_settings().telegram_webhook_secret
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    # No secret configured = the transport is off; reject everything rather
    # than run an unauthenticated endpoint that accepts forged updates.
    if not expected or not _secrets.compare_digest(given, expected):
        log.warning("rejected: bad or missing Telegram secret token")
        raise HTTPException(status_code=403, detail="Invalid secret token")


def _address(chat: dict[str, Any]) -> str:
    return f"telegram:{chat.get('id')}"


def _is_authorized(address: str) -> bool:
    return address.lower() in set(get_settings().authorized_numbers_list)


def _extract_media(message: dict[str, Any]) -> list[tuple[str, str]]:
    """Attachments as (pseudo-URL, content-type), the shape the pipeline expects.

    A Telegram photo arrives as an array of sizes — the last is the largest,
    and it is the one worth rendering from. A document with an image/video mime
    is someone sending "as file" for full quality; treat it as the attachment
    it is rather than ignoring it.
    """
    media: list[tuple[str, str]] = []
    if photos := message.get("photo"):
        media.append((f"telegram-file:{photos[-1]['file_id']}", "image/jpeg"))
    if voice := message.get("voice"):
        media.append((f"telegram-file:{voice['file_id']}", voice.get("mime_type") or "audio/ogg"))
    if audio := message.get("audio"):
        media.append((f"telegram-file:{audio['file_id']}", audio.get("mime_type") or "audio/mpeg"))
    if video := message.get("video"):
        media.append((f"telegram-file:{video['file_id']}", video.get("mime_type") or "video/mp4"))
    if document := message.get("document"):
        mime = document.get("mime_type") or ""
        if mime.startswith(("image/", "video/")):
            media.append((f"telegram-file:{document['file_id']}", mime))
    return media


@router.post("")
async def incoming_update(request: Request, background: BackgroundTasks) -> JSONResponse:
    _verify(request)
    update: dict[str, Any] = await request.json()

    # The bot was just added to (or promoted in) a chat — usually the team
    # group being set up. Say hello with the chat id so wiring it into the
    # allowlist is a copy-paste, not a spelunk through Railway logs.
    if member := update.get("my_chat_member"):
        chat = member.get("chat") or {}
        address = _address(chat)
        status = ((member.get("new_chat_member") or {}).get("status")) or ""
        log.info("chat membership change", extra={"chat": address, "status": status})
        if status in ("member", "administrator") and not _is_authorized(address):
            await telegram_client.try_send_text(
                address, _UNKNOWN_CHAT_REPLY.format(chat_id=chat.get("id"))
            )
        return JSONResponse({"ok": True})

    message = update.get("message")
    if not message:
        # Edits, reactions, channel posts — nothing the approval flow acts on.
        log.info("ignoring update without message", extra={"keys": sorted(update.keys())})
        return JSONResponse({"ok": True})

    chat = message.get("chat") or {}
    address = _address(chat)
    body = str(message.get("text") or message.get("caption") or "")
    media = _extract_media(message)

    if not _is_authorized(address):
        log.warning("message from unauthorized chat", extra={"chat": address})
        await telegram_client.try_send_text(
            address, _UNKNOWN_CHAT_REPLY.format(chat_id=chat.get("id"))
        )
        return JSONResponse({"ok": True})

    # /start is Telegram furniture (the button every new private chat shows),
    # not something the intent classifier should puzzle over.
    if body.startswith("/start"):
        body = "hello"

    chat_id = chat.get("id")
    message_sid = f"tg:{chat_id}:{message.get('message_id')}"
    # Same shape the outbound side records, so a swipe-reply to a preview
    # resolves to its post through the existing by_sid lookup.
    reply = message.get("reply_to_message") or {}
    reply_to_sid = f"tg:{chat_id}:{reply['message_id']}" if reply.get("message_id") else None

    log.info(
        "inbound message",
        extra={
            "from": address,
            "num_media": len(media),
            "message_sid": message_sid,
            "reply_to_sid": reply_to_sid,
        },
    )
    background.add_task(
        on_demand.handle_incoming_message,
        address,
        body,
        media,
        message_sid=message_sid,
        reply_to_sid=reply_to_sid,
    )
    return JSONResponse({"ok": True})
