"""Previews that were built but never arrived, and the job that re-sends them.

Delivery and work are separate concerns: a render can succeed, be stored, and
still fail to reach anybody because the 24-hour WhatsApp window closed, the
sandbox join lapsed, or the account hit its daily message cap. Before this, such
a failure was written to the log and forgotten — the operator asked for a change,
the change happened, and they never saw it. Nothing in the system remembered
that a delivery was owed.

Each post therefore carries the set of recipients still owed a preview, in
``render_meta['undelivered']``. A scheduler job drains it: one attempt per post
per pass, oldest first, stopping at the first failure so a still-closed window
does not burn the whole message quota in one sweep.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.db import posts
from app.logging_config import get_logger
from app.messaging import twilio_client

log = get_logger("app.workflows.redelivery")

META_KEY = "undelivered"
# Posts whose preview never landed are worth chasing; anything already approved,
# published or cancelled has moved on and must not resurface in someone's chat.
LIVE_STATUSES = ("pending_approval", "draft")


async def record(post_id: str, phone: str, *, delivered: bool) -> None:
    """Add or clear this recipient's claim on a preview of ``post_id``."""
    try:
        post = await asyncio.to_thread(posts.get, post_id)
        meta = dict((post or {}).get("render_meta") or {})
        owed = [p for p in (meta.get(META_KEY) or []) if p != phone]
        if not delivered:
            owed.append(phone)
        if owed:
            meta[META_KEY] = owed
        else:
            meta.pop(META_KEY, None)
        await asyncio.to_thread(posts.set_render_meta, post_id, meta)
    except Exception as exc:  # noqa: BLE001 — bookkeeping must never break the turn
        log.error("could not record delivery", extra={"post_id": post_id, "error": str(exc)[:200]})


def _owed(post: dict[str, Any]) -> list[str]:
    return list(((post.get("render_meta") or {}).get(META_KEY)) or [])


async def pending() -> list[dict[str, Any]]:
    """Live posts still owing someone a preview, oldest first."""
    rows: list[dict[str, Any]] = []
    for status in LIVE_STATUSES:
        rows.extend(await asyncio.to_thread(posts.list_by_status, status))
    owing = [r for r in rows if _owed(r) and r.get("image_url")]
    return sorted(owing, key=lambda r: str(r.get("created_at") or ""))


async def retry_undelivered() -> int:
    """Re-send every preview that never arrived. Returns how many got through.

    Stops at the first failure: the usual cause is an account-wide condition (a
    closed window, an exhausted quota), so continuing would spend the remaining
    quota re-failing and leave nothing for live traffic.
    """
    delivered = 0
    for post in await pending():
        meta = post.get("render_meta") or {}
        title = ((meta.get("calendar") or {}).get("title")) or "your draft"
        caption = f"📬 Catching up — this preview never reached you:\n\n{post.get('caption') or ''}"
        for phone in _owed(post):
            # By definition the window was shut when this failed, and it usually
            # still is — so the retry goes through send_preview, which falls back
            # to the approved template rather than re-failing the same way.
            sid = await twilio_client.try_send_preview(
                phone,
                caption.strip(),
                str(post["image_url"]),
                identity=title,
                caption=str(post.get("caption") or ""),
                post_id=str(post["id"]),
            )
            if sid is None:
                log.info(
                    "re-delivery still failing; leaving the rest queued",
                    extra={"post_id": post["id"], "to": phone},
                )
                return delivered
            await record(str(post["id"]), phone, delivered=True)
            delivered += 1
            log.info(
                "re-delivered preview",
                extra={"post_id": post["id"], "to": phone, "title": title},
            )
    return delivered
