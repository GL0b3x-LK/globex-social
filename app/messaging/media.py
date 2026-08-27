"""Download media (photos) Karen attaches to a WhatsApp message.

Twilio media URLs require account basic-auth and redirect (302) to a short-lived
storage URL, so we follow redirects; httpx drops the auth header on the cross-host
hop, which is exactly what the pre-signed storage URL wants.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.messaging.media")


async def download_media(url: str, *, timeout: float = 10.0) -> tuple[bytes, str]:
    """Fetch an attachment from whichever transport it arrived on.

    Telegram attachments are carried as "telegram-file:<file_id>" pseudo-URLs
    (the real download URL embeds the bot token, so it is never stored) and
    resolved via getFile at download time; anything else is a Twilio media URL.
    Dispatching here — not in the workflows — is what keeps "download the photo
    Karen attached" a one-liner regardless of the app she sent it from.
    """
    if url.startswith("telegram-file:"):
        from app.messaging import telegram_client  # local import: avoid cycle via history

        return await telegram_client.download_file(
            url.removeprefix("telegram-file:"), timeout=timeout
        )
    return await download_twilio_media(url, timeout=timeout)


async def download_twilio_media(url: str, *, timeout: float = 10.0) -> tuple[bytes, str]:
    """Fetch a Twilio media URL; return (bytes, content_type). Raises on HTTP error.

    `timeout` is bumped for video (larger payloads) vs photos/voice notes.
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, auth=(settings.twilio_account_sid, settings.twilio_auth_token))
        resp.raise_for_status()
    content_type = resp.headers.get("content-type", "application/octet-stream")
    log.info("downloaded media", extra={"bytes": len(resp.content), "content_type": content_type})
    return resp.content, content_type
