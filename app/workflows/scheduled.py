"""Calendar-driven drafting and publishing (Phase 6).

Daily: pick calendar entries inside the lead window, generate copy, render on the
entry's approved template with a matching photo from the curated asset pool, and
send the preview to the approver's WhatsApp. The normal approval flow takes over
from there — NOTHING publishes without an explicit "approve" (contract rule).

Approved scheduled posts wait for their calendar date: handle_approval sees
render_meta.publish_on in the future and holds; publish_due_posts() fires the
Blotato publish on the day itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.ai import generator
from app.ai.generator import ContentCategory
from app.config import get_settings
from app.db import calendar_source, posts
from app.db.calendar_source import EVENT_TYPE, CalendarEntry
from app.logging_config import get_logger
from app.messaging import twilio_client
from app.publishing import publisher
from app.templates.catalog import CALENDAR_TEMPLATE_ALIASES
from app.workflows import messages

log = get_logger("app.workflows.scheduled")

_POOL_DIR = Path(__file__).parent.parent / "data" / "asset_pool"

# Calendar category -> content-generation prompt family.
_CATEGORY_PROMPTS: dict[str, ContentCategory] = {
    "product": ContentCategory.product_spotlight,
    "brand": ContentCategory.promotional,
    "packaging": ContentCategory.branded_packaging,
    "holiday": ContentCategory.holiday,
    "tradeshow": ContentCategory.trade_show,
    "milestone": ContentCategory.milestone,
    "special": ContentCategory.announcement,
}


def approver_phone() -> str:
    """The approval recipient — first entry of the allowlist (Karen in production)."""
    return get_settings().authorized_numbers_list[0]


@lru_cache(maxsize=1)
def _pool() -> list[dict[str, Any]]:
    doc = json.loads((_POOL_DIR / "pool.json").read_text(encoding="utf-8"))
    return doc["assets"]


def pick_photo_for_text(text: str, category: str = "brand", *, seed_key: str = "") -> Path:
    """Best-tag-match pool asset for arbitrary text; deterministic per seed_key.

    Branded packaging shots outrank raw-product photography — Len rejected
    graphic carcass imagery in the design rounds, so cartons/retail bags are the
    default face of product posts and raw shots only win on a strong subject
    match. The operator can always swap the image via WhatsApp before approving.
    """
    text = f"{category} {text}".lower()
    scored: list[tuple[float, int, dict[str, Any]]] = []
    seed = int(hashlib.sha256((seed_key or text).encode()).hexdigest()[:8], 16)
    for i, asset in enumerate(_pool()):
        if asset["file"].startswith("placeholder") and category != "milestone":
            continue
        score = float(sum(1 for t in asset["tags"] if t in text))
        if asset["file"].startswith("pack-") and category in ("product", "packaging"):
            score += 1.5  # branded presentation beats raw meat at equal subject match
        if asset["file"].startswith("prod-") and category != "product":
            score -= 1.0  # raw shots never front brand/holiday/show posts
        jitter = (seed + i) % 7  # stable variety among equal scorers
        scored.append((score, jitter, asset))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return _POOL_DIR / scored[0][2]["file"]


def pick_photo(entry: CalendarEntry) -> Path:
    """Pool photo for a calendar entry (see pick_photo_for_text)."""
    return pick_photo_for_text(
        f"{entry.title} {entry.gist}", entry.category, seed_key=entry.event_id
    )


def _entry_brief(entry: CalendarEntry) -> str:
    """The generation brief — the calendar row is the creative instruction."""
    when = entry.post_date or entry.planned_date
    return (
        f"Scheduled calendar post for {when.strftime('%A %d %B %Y')}.\n"
        f"Theme: {entry.title}\n"
        f"What the post should say: {entry.gist}\n"
        f"Marketing purpose: {entry.purpose}\n"
        "Write the caption and on-image text to match this brief exactly."
    )


async def draft_calendar_entry(entry: CalendarEntry) -> None:
    from app.workflows.on_demand import _finalize_preview  # local import: avoid cycle

    when = entry.post_date or entry.planned_date
    category = _CATEGORY_PROMPTS.get(entry.category, ContentCategory.promotional)
    generated = await generator.generate_post(
        category,
        context={
            "post_date": when.isoformat(),
            "calendar_title": entry.title,
            "marketing_purpose": entry.purpose,
        },
        user_message=_entry_brief(entry),
    )
    # The calendar's template column is authoritative — never the model's choice.
    generated.template_variant = CALENDAR_TEMPLATE_ALIASES.get(entry.template, entry.template)

    photo = pick_photo(entry)
    prefix = (
        f"🗓 Scheduled post — goes out {when.strftime('%a %d %b')} once you approve\n"
        f"({entry.title})\n\n"
    )
    if photo.name.startswith("placeholder"):
        prefix += "📷 Placeholder image — reply with the employee's photo to swap it in.\n\n"
    await _finalize_preview(
        approver_phone(),
        _entry_brief(entry),
        generated,
        image_bytes=photo.read_bytes(),
        image_media_type="image/jpeg",
        treatment="calendar",
        event=(EVENT_TYPE, entry.event_id),
        extra_render_meta={
            "publish_on": when.isoformat(),
            "calendar": {
                "week": entry.week,
                "title": entry.title,
                "category": entry.category,
                "template": entry.template,
            },
        },
        caption_prefix=prefix,
    )
    log.info(
        "calendar draft sent",
        extra={"event_id": entry.event_id, "title": entry.title, "date": str(when)},
    )


async def draft_due_posts(today: date | None = None) -> int:
    """Draft every calendar entry inside the lead window that has no post yet."""
    settings = get_settings()
    today = today or date.today()
    due = calendar_source.entries_due(today, settings.draft_lead_days)
    fresh = await asyncio.to_thread(calendar_source.undrafted, due)
    for entry in sorted(fresh, key=lambda e: e.post_date or e.planned_date):
        try:
            await draft_calendar_entry(entry)
        except Exception:
            log.exception("calendar draft failed", extra={"title": entry.title})
    return len(fresh)


async def publish_due_posts(today: date | None = None) -> int:
    """Publish approved scheduled posts whose calendar date has arrived."""
    today = today or date.today()
    approved = await asyncio.to_thread(posts.list_by_status, "approved")
    published = 0
    for post in approved:
        meta = post.get("render_meta") or {}
        publish_on = meta.get("publish_on")
        if not publish_on or date.fromisoformat(publish_on) > today:
            continue
        results = await publisher.publish_post(post["id"])
        published += 1
        title = (meta.get("calendar") or {}).get("title", "scheduled post")
        await twilio_client.send_text(
            approver_phone(),
            f"🚀 Published today's scheduled post ({title})\n" + messages.publish_status(results),
        )
    return published
