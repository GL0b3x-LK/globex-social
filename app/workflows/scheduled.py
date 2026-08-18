"""Calendar-driven drafting and publishing (Phase 6).

Weekdays at 7am New York: pick the calendar entries due between now and the next
working day — so a Tuesday post previews on Monday and a Monday post on Friday,
never over the weekend when nobody is there to approve it — generate copy, render
on the entry's approved template with a matching photo from the curated asset
pool, and send the preview to the approver's WhatsApp. That gives them a full
business day to edit and approve. The normal approval flow takes over from there
— NOTHING publishes without an explicit "approve" (contract rule).

Approving does not publish. An approved scheduled post waits for its moment —
1am New York on its calendar date — however early the yes came: handle_approval
sees render_meta.publish_on still ahead and holds, and publish_due_posts() fires
the Blotato publish when the day arrives.

Every date here is the client's, via app.clock: the server runs UTC, whose day
rolls over at 8pm New York — four hours before a post is due to go out.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import clock
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
    """The catalogue, less any entry whose photograph is not actually on disk.

    The picker hands its result straight to ``read_bytes``, so a catalogue entry
    with no file behind it is a crashed draft rather than a missing picture — and
    a post that never drafts is a calendar entry retired without anyone seeing
    it. Orphans are real: a generated shot was rejected on inspection and deleted
    while its entry stayed behind.
    """
    doc = json.loads((_POOL_DIR / "pool.json").read_text(encoding="utf-8"))
    usable: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    for asset in doc["assets"]:
        (usable if (_POOL_DIR / asset["file"]).exists() else orphans).append(asset)
    if orphans:
        log.error(
            "pool entries have no image on disk; skipping them",
            extra={"files": [a["file"] for a in orphans]},
        )
    return usable


def recently_used(limit: int = 12) -> frozenset[str]:
    """Pool files the last few posts already used.

    The picker had no memory at all, so any two briefs that scored the same asset
    highest got the identical photograph — the repetition the testers saw. Read
    back off the posts themselves so the fact lives in one place (render_meta)
    rather than a second store that can drift out of step with what was sent.
    """
    try:
        rows = posts.recent(limit)
    except Exception as exc:  # noqa: BLE001 — variety is a nicety, drafting is not
        log.warning("could not read recent posts for variety", extra={"error": str(exc)[:120]})
        return frozenset()
    used: set[str] = set()
    for row in rows:
        meta = row.get("render_meta") or {}
        if isinstance(meta, dict) and meta.get("pool_asset"):
            used.add(str(meta["pool_asset"]))
    return frozenset(used)


def pick_photo_for_text(
    text: str,
    category: str = "brand",
    *,
    seed_key: str = "",
    exclude: frozenset[str] = frozenset(),
) -> Path:
    """Best-tag-match pool asset for arbitrary text; deterministic per seed_key.

    Branded packaging shots outrank raw-product photography — Len rejected
    graphic carcass imagery in the design rounds, so cartons/retail bags are the
    default face of product posts and raw shots only win on a strong subject
    match. The operator can always swap the image via WhatsApp before approving.

    ``exclude`` skips photographs used recently. It walks DOWN the ranking rather
    than reshuffling it, so the picture is still the best available match for the
    brief — just not the same best match as the last one. If everything that
    scores has been used lately, repeating beats refusing to draft.
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
    fresh = next((s for s in scored if s[2]["file"] not in exclude), None)
    return _POOL_DIR / (fresh or scored[0])[2]["file"]


def pick_photo(entry: CalendarEntry, *, exclude: frozenset[str] = frozenset()) -> Path:
    """Pool photo for a calendar entry (see pick_photo_for_text)."""
    return pick_photo_for_text(
        f"{entry.title} {entry.gist}", entry.category, seed_key=entry.event_id, exclude=exclude
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


async def draft_calendar_entry(entry: CalendarEntry, *, in_sequence: bool = False) -> None:
    """Draft one calendar entry and send it for approval.

    ``in_sequence`` is the sequential run, which walks the approved order one
    post a day instead of waiting months for each entry's real calendar date.
    The cadence it imitates is the client's exactly: drafted at 7am, dated
    TOMORROW, so approving parks it until 1am the next morning like any other
    scheduled post. It used to carry today's date and publish on approval —
    which made it a demo of a flow the client will never see.
    """
    from app.workflows.on_demand import _finalize_preview  # local import: avoid cycle

    when = (
        clock.today() + timedelta(days=1)
        if in_sequence
        else (entry.post_date or entry.planned_date)
    )
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

    photo = pick_photo(entry, exclude=await asyncio.to_thread(recently_used))
    # The time is spelled out because approving is not publishing: the post waits
    # for its slot however early the yes arrives, and an approver who expects it
    # to go out on approval reads the delay as a failure.
    go_live = clock.publish_moment(when).strftime("%a %d %b, %-I%p").replace("AM", "am")
    if in_sequence:
        prefix = (
            f"🗓 *Post {entry.seq + 1}/{total_planned()}* — approve any time today; "
            f"it goes out {go_live}\n"
            f"({entry_title(entry)} · week {entry.week} · {entry.category})\n\n"
        )
    else:
        prefix = (
            f"🗓 Scheduled post — approve any time before then; it goes out {go_live}\n"
            f"({entry_title(entry)})\n\n"
        )
    prefix += sheet_note
    # The one line that names this post when it goes out as a template — the
    # template body reads "Scheduled post {identity} — approve any time before it
    # goes out", so it carries what the free-form prefix would have said.
    identity = f"{entry.seq + 1}/{total_planned()}: {entry_title(entry)} (out {go_live})"
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
        identity=identity,
        event=(EVENT_TYPE, entry.event_id),
        extra_render_meta={
            "publish_on": when.isoformat(),
            "caption_locked": caption_locked,
            # Which pool shot fronted this post, so the next draft can pick a
            # different one (see recently_used).
            "pool_asset": photo.name,
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


async def draft_next_in_sequence() -> bool:
    """Draft the single next un-drafted calendar post, in the approved order.

    Walks the client-approved sequence rather than the calendar's dates, so the
    team sees the real run of posts without waiting a year for it — but at the
    real cadence (7am draft, 1am publish the next day), so what they are
    reviewing is the flow the client will actually get. Returns False when the
    calendar is exhausted, which stops the run rather than looping.
    """
    entries = sorted(calendar_source.load_calendar(), key=lambda e: e.seq)
    fresh = await asyncio.to_thread(calendar_source.undrafted, list(entries))
    if not fresh:
        log.info("sequential run: calendar exhausted, nothing left to draft")
        return False
    entry = fresh[0]
    await draft_calendar_entry(entry, in_sequence=True)
    log.info(
        "sequential draft sent",
        extra={"seq": entry.seq, "title": entry.title, "remaining": len(fresh) - 1},
    )
    return True


async def drafted_since(moment: datetime) -> bool:
    """Has any calendar post been drafted since ``moment``?

    Answers "did today's slot actually run?" after a restart. APScheduler holds
    its jobs in memory, so a boot recomputes the next fire time from now — a
    deploy at 9am on a 7am grid does not run the missed slot, it schedules
    tomorrow's, and the day's post is lost with nothing in the logs saying so.
    That was survivable at one post an hour; at one a day it is the whole day.
    """
    recent = await asyncio.to_thread(posts.recent, 40)
    for post in recent:
        if post.get("event_type") != EVENT_TYPE:
            continue
        created = post.get("created_at")
        if created and datetime.fromisoformat(created) >= moment:
            return True
    return False


async def draft_due_posts(today: date | None = None) -> int:
    """Draft every calendar entry up to the next working day that has no post yet.

    The window reaches the next WORKING day rather than a fixed number of days
    ahead, so Friday's 7am run covers Saturday, Sunday and Monday: a Monday post
    is previewed while somebody is still at work to approve it. Today is inside
    the window too — a post that somehow reached its own date undrafted is better
    late than silently skipped.
    """
    today = today or clock.today()
    lead = (clock.next_working_day(today) - today).days
    due = calendar_source.entries_due(today, lead)
    fresh = await asyncio.to_thread(calendar_source.undrafted, due)
    for entry in sorted(fresh, key=lambda e: e.post_date or e.planned_date):
        try:
            await draft_calendar_entry(entry)
        except Exception:
            log.exception("calendar draft failed", extra={"title": entry.title})
    return len(fresh)


async def publish_due_posts(today: date | None = None) -> int:
    """Publish approved scheduled posts whose calendar date has arrived.

    Runs at 1am New York. A post whose date has passed still publishes — a
    yes given after the sweep, or a day the service spent restarting, must not
    leave an approved post stranded forever.
    """
    today = today or clock.today()
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
