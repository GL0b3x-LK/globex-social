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

from app.ai import generator, style
from app.ai.generator import ContentCategory
from app.config import get_settings
from app.db import calendar_source, posts
from app.db.calendar_source import EVENT_TYPE, CalendarEntry
from app.logging_config import get_logger
from app.messaging import twilio_client
from app.publishing import calendar_sheet, publisher
from app.templates.catalog import CALENDAR_TEMPLATE_ALIASES
from app.video import library
from app.workflows import messages

log = get_logger("app.workflows.scheduled")

_POOL_DIR = Path(__file__).parent.parent / "data" / "asset_pool"


@lru_cache(maxsize=1)
def total_planned() -> int:
    """How many posts the approved calendar holds — numbers the test previews."""
    return len(calendar_source.load_calendar())


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


def approver_phones() -> list[str]:
    """Everyone who should see a scheduled draft.

    One name in production, both testers during the internal run — either can
    answer, because they are looking at the same post.
    """
    return get_settings().approval_recipients_list or [approver_phone()]


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


def entry_title(entry: CalendarEntry) -> str:
    """The entry's title in the client's capitalisation ("Thank You, SIAL Paris").

    Normalised HERE rather than in calendar.json because ``event_id`` is
    ``uuid5(seq|title)`` — rewriting the stored titles would give every entry a
    new id and re-draft the twenty posts already sent as duplicates. The stored
    title stays the stable key; everything a person or the model reads is
    corrected on the way out.
    """
    return style.title_case(style.fix_terms(entry.title))


def _entry_brief(entry: CalendarEntry) -> str:
    """The generation brief — the calendar row is the creative instruction."""
    when = entry.post_date or entry.planned_date
    return (
        f"Scheduled calendar post for {when.strftime('%A %d %B %Y')}.\n"
        f"Theme: {entry_title(entry)}\n"
        f"What the post should say: {entry.gist}\n"
        f"Marketing purpose: {entry.purpose}\n"
        "Write the caption and on-image text to match this brief exactly."
    )


async def draft_calendar_entry(entry: CalendarEntry, *, publish_today: bool = False) -> None:
    """Draft one calendar entry and send it for approval.

    ``publish_today`` is the internal test run: the post carries today's date, so
    approving it publishes straight away instead of parking it until its real
    calendar date months from now. The approval gate itself is unchanged.
    """
    from app.workflows.on_demand import _finalize_preview  # local import: avoid cycle

    when = date.today() if publish_today else (entry.post_date or entry.planned_date)
    category = _CATEGORY_PROMPTS.get(entry.category, ContentCategory.promotional)
    generated = await generator.generate_post(
        category,
        context={
            "post_date": when.isoformat(),
            "calendar_title": entry_title(entry),
            "marketing_purpose": entry.purpose,
        },
        user_message=_entry_brief(entry),
    )
    # The calendar's template column is authoritative — never the model's choice.
    generated.template_variant = CALENDAR_TEMPLATE_ALIASES.get(entry.template, entry.template)

    # The sheet's "Exact Caption" column outranks the model: anything the client
    # wrote there IS the caption, posted verbatim. Hashtags are cleared so the
    # publish-time join cannot append anything to their words.
    sheet_note = ""
    sheet_caption = await calendar_sheet.exact_caption(entry.title)
    caption_locked = sheet_caption is not None
    if sheet_caption is not None:
        generated.caption = sheet_caption
        generated.hashtags = []
        sheet_note = "📋 Caption supplied in the calendar sheet — posting it exactly as written.\n"
        struck = library.banned_terms_in(sheet_caption)
        if struck:
            # Verbatim means verbatim, but the approver decides with eyes open.
            sheet_note += (
                f"⚠️ It contains {', '.join(repr(t) for t in struck)} — on the struck "
                "list, but posting as instructed if you approve.\n"
            )
        sheet_note += "\n"

    photo = pick_photo(entry)
    if publish_today:
        prefix = (
            f"🧪 *Test post {entry.seq + 1}/{total_planned()}* — publishes as soon as "
            f"you approve\n({entry_title(entry)} · week {entry.week} · {entry.category})\n\n"
        )
    else:
        prefix = (
            f"🗓 Scheduled post — goes out {when.strftime('%a %d %b')} once you approve\n"
            f"({entry_title(entry)})\n\n"
        )
    prefix += sheet_note
    is_placeholder = photo.name.startswith("placeholder")
    if is_placeholder:
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
            "caption_locked": caption_locked,
            # Marked so an edit never sends this stand-in card to the image model:
            # asked to "improve" a gray placeholder, it invents a person and the
            # post ends up carrying a fabricated face for a named employee.
            "photo_is_placeholder": is_placeholder,
            "calendar": {
                "week": entry.week,
                "title": entry_title(entry),
                "category": entry.category,
                "template": entry.template,
            },
        },
        caption_prefix=prefix,
        recipients=approver_phones(),
    )
    log.info(
        "calendar draft sent",
        extra={"event_id": entry.event_id, "title": entry.title, "date": str(when)},
    )


async def draft_next_for_test() -> bool:
    """Draft the single next un-drafted calendar post, for the internal test run.

    Walks the client-approved order rather than the calendar's dates, so the team
    sees the real sequence of posts without waiting a year for it. Returns False
    when the calendar is exhausted, which stops the run rather than looping.
    """
    entries = sorted(calendar_source.load_calendar(), key=lambda e: e.seq)
    fresh = await asyncio.to_thread(calendar_source.undrafted, list(entries))
    if not fresh:
        log.info("test run: calendar exhausted, nothing left to draft")
        return False
    entry = fresh[0]
    await draft_calendar_entry(entry, publish_today=True)
    log.info(
        "test draft sent",
        extra={"seq": entry.seq, "title": entry.title, "remaining": len(fresh) - 1},
    )
    return True


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
