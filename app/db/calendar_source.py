"""The 52-week content calendar the scheduler drafts from.

Source of truth is app/data/calendar.json (the client-approved draft; regenerated
when Mike/Len request tweaks). Drafted state is tracked in the posts table via a
deterministic per-entry UUID (event_type='calendar'), so no extra table is needed
and re-running the draft job is idempotent. When SUPABASE_DB_URL lands we can
promote this into a calendar_posts table without changing callers.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from app.db import posts as posts_db

CALENDAR_PATH = Path(__file__).parent.parent / "data" / "calendar.json"

# Stable namespace for calendar entry ids — never change, or drafted-state
# tracking resets and every entry drafts again.
_NS = uuid.UUID("6c1e6b48-9a3d-4b62-a8a4-3f2b7d1c9e55")

EVENT_TYPE = "calendar"


@dataclass(frozen=True)
class CalendarEntry:
    week: int
    post_date: date
    category: str
    title: str
    gist: str
    template: str  # calendar name, e.g. "TS-p2-cut-navyborder_4x5"
    purpose: str

    @property
    def event_id(self) -> str:
        """Deterministic id: same entry -> same id across runs and deploys."""
        return str(uuid.uuid5(_NS, f"{self.post_date.isoformat()}|{self.title}"))


@lru_cache(maxsize=1)
def load_calendar() -> tuple[CalendarEntry, ...]:
    doc = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    return tuple(
        CalendarEntry(
            week=p["week"],
            post_date=date.fromisoformat(p["date"]),
            category=p["category"],
            title=p["title"],
            gist=p["gist"],
            template=p["template"],
            purpose=p["purpose"],
        )
        for p in doc["posts"]
    )


def entries_due(today: date, lead_days: int) -> list[CalendarEntry]:
    """Entries whose post_date falls inside [today, today + lead_days]."""
    horizon = today + timedelta(days=lead_days)
    return [e for e in load_calendar() if today <= e.post_date <= horizon]


def undrafted(entries: list[CalendarEntry]) -> list[CalendarEntry]:
    """Filter out entries that already have a post row (any status)."""
    fresh: list[CalendarEntry] = []
    for e in entries:
        if not posts_db.find_for_event(e.event_id, EVENT_TYPE):
            fresh.append(e)
    return fresh
