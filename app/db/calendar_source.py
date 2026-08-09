"""The 52-week content calendar the scheduler drafts from.

Source of truth is app/data/calendar.json (the client-approved draft; regenerated
when Mike/Len request tweaks). Entries carry a ``planned_date`` — the plan the
client signed off — but the LIVE schedule is derived at read time from
``settings.calendar_launch_date``:

  * launch date unset  -> the calendar is dormant. No entry is ever due, so the
    scheduler cannot draft or publish anything and no post is silently skipped
    while the launch date is still being decided.
  * launch date set    -> the year re-flows from that day. Anchored entries
    (holidays, trade shows, anniversaries, season-tied posts) keep their real
    dates because a Thanksgiving post can't move; anything still in the past on
    launch day is dropped, deliberately and visibly. Floating evergreen entries
    (product / brand / packaging) refill the remaining Mon/Wed/Fri slots in the
    client-approved order, so none of them are lost no matter when we launch.

Drafted state is tracked in the posts table via a deterministic per-entry UUID
(event_type='calendar'), so re-running the draft job is idempotent and no extra
table is needed. When SUPABASE_DB_URL lands we can promote this into a
calendar_posts table without changing callers.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.db import posts as posts_db
from app.logging_config import get_logger

log = get_logger("app.db.calendar")

CALENDAR_PATH = Path(__file__).parent.parent / "data" / "calendar.json"

# Stable namespace for calendar entry ids — never change, or drafted-state
# tracking resets and every entry drafts again.
_NS = uuid.UUID("6c1e6b48-9a3d-4b62-a8a4-3f2b7d1c9e55")

EVENT_TYPE = "calendar"

_POST_WEEKDAYS = (0, 2, 4)  # Mon/Wed/Fri — Tue/Thu stay reserved for video


@dataclass(frozen=True)
class CalendarEntry:
    seq: int  # position in the client-approved order
    week: int
    planned_date: date  # the approved plan
    category: str
    title: str
    gist: str
    template: str  # calendar name, e.g. "TS-p2-cut-navyborder_4x5"
    purpose: str
    anchored: bool  # real-world-dated (holiday/show/anniversary/season)
    post_date: date | None = None  # live date; only set once a launch date exists

    @property
    def event_id(self) -> str:
        """Deterministic id: same entry -> same id across runs and deploys.

        Keyed on the entry itself (not its date) so re-anchoring the calendar to a
        launch date never re-drafts a post that already went to WhatsApp.
        """
        return str(uuid.uuid5(_NS, f"{self.seq}|{self.title}"))


@lru_cache(maxsize=1)
def load_calendar() -> tuple[CalendarEntry, ...]:
    """Every approved entry, unscheduled (post_date is None)."""
    doc = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    return tuple(
        CalendarEntry(
            seq=p["seq"],
            week=p["week"],
            planned_date=date.fromisoformat(p["planned_date"]),
            category=p["category"],
            title=p["title"],
            gist=p["gist"],
            template=p["template"],
            purpose=p["purpose"],
            anchored=p["anchored"],
        )
        for p in doc["posts"]
    )


def launch_date() -> date | None:
    raw = get_settings().calendar_launch_date
    return date.fromisoformat(raw) if raw else None


def _slots_from(start: date, count: int) -> list[date]:
    """The next `count` Mon/Wed/Fri dates, starting on or after `start`."""
    slots: list[date] = []
    day = start
    while len(slots) < count:
        if day.weekday() in _POST_WEEKDAYS:
            slots.append(day)
        day += timedelta(days=1)
    return slots


def schedule(launch: date | None = None) -> list[CalendarEntry]:
    """The live schedule: entries with real post_dates, ordered by date.

    Returns [] when no launch date is configured — the calendar is dormant.
    """
    launch = launch or launch_date()
    if launch is None:
        return []

    entries = load_calendar()
    anchored = [e for e in entries if e.anchored and e.planned_date >= launch]
    dropped = [e for e in entries if e.anchored and e.planned_date < launch]
    floating = [e for e in entries if not e.anchored]

    if dropped:
        log.info(
            "calendar launch drops past-dated anchored entries",
            extra={"launch": str(launch), "dropped": len(dropped)},
        )

    taken = {e.planned_date for e in anchored}
    scheduled = [replace(e, post_date=e.planned_date) for e in anchored]

    # Floating entries fill every remaining Mon/Wed/Fri slot, in approved order.
    slots = [d for d in _slots_from(launch, len(entries) + len(taken) + 8) if d not in taken][
        : len(floating)
    ]
    scheduled += [
        replace(e, post_date=d)
        for e, d in zip(sorted(floating, key=lambda e: e.seq), slots, strict=True)
    ]
    return sorted(scheduled, key=lambda e: (e.post_date or date.max, e.seq))


def dropped_by_launch(launch: date) -> list[CalendarEntry]:
    """Anchored entries a given launch date would skip (their moment has passed)."""
    return [e for e in load_calendar() if e.anchored and e.planned_date < launch]


def entries_due(today: date, lead_days: int) -> list[CalendarEntry]:
    """Scheduled entries whose post_date falls inside [today, today + lead_days]."""
    horizon = today + timedelta(days=lead_days)
    return [e for e in schedule() if e.post_date and today <= e.post_date <= horizon]


def undrafted(entries: list[CalendarEntry]) -> list[CalendarEntry]:
    """Filter out entries that already have a post row (any status)."""
    fresh: list[CalendarEntry] = []
    for e in entries:
        if not posts_db.find_for_event(e.event_id, EVENT_TYPE):
            fresh.append(e)
    return fresh
