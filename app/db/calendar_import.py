"""Turn the client's calendar workbook into the JSON the scheduler drafts from.

The September 2026 "Internal Version for Globex Only" sheet has four columns —
Date, Category, Key Feature/Theme, Exact Caption — against the seven fields a
CalendarEntry carries. The missing ones are derived here, in one place, from
the client's own conventions (Notes sheet: holidays on TS-p3-editorial,
milestones on MS-3-anniv-photo, four approved templates only):

  * template  — by category, and for trade shows by phase: a "Meet us" /
                "Save the date" teaser, a "Live at" day-one post, a "Thank you"
                recap each get the layout the July sign-off used for that beat.
  * gist      — the theme, plus the caption's opening line when the client
                wrote one, so the on-image text is composed to match it.
  * anchored  — every row is dated by the client, so every row is anchored.

Rows without a date are the client's working notes ("Missing Fish — anything
else Len sent?"), not posts; they are carried out as `client_questions` so
they are answered rather than silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl

from app.logging_config import get_logger

log = get_logger("app.db.calendar_import")

SHEET = "52-Week Calendar"

_CATEGORY = {
    "holiday": "holiday",
    "trade show": "tradeshow",
    "tradeshow": "tradeshow",
    "milestone": "milestone",
    "annual": "special",
    "special": "special",
}

_TEMPLATE = {
    "holiday": "TS-p3-editorial_4x5",
    "milestone": "MS-3-anniv-photo_4x5",
    "special": "TS-p3-editorial_4x5",
}
_SHOW_TEMPLATE = {
    "teaser": "TS-p1-bolddip_4x5",
    "live": "TS-p3-editorial_4x5",
    "recap": "TS-p2-cut-navyborder_4x5",
}
_PURPOSE = {
    "holiday": "Seasonal relevance and brand warmth",
    "milestone": "Company and people milestone",
    "special": "Annual announcement",
    "teaser": "Pre-show meeting demand-gen",
    "live": "Live show presence",
    "recap": "Post-show thank-you and relationship",
}


@dataclass(frozen=True)
class ImportedEntry:
    seq: int
    week: int
    planned_date: str
    category: str
    title: str
    gist: str
    template: str
    purpose: str
    anchored: bool
    exact_caption: str | None


def show_phase(title: str) -> str:
    t = title.lower()
    if t.startswith("live at"):
        return "live"
    if t.startswith("thank you"):
        return "recap"
    return "teaser"


def _template_for(category: str, title: str) -> tuple[str, str]:
    if category == "tradeshow":
        phase = show_phase(title)
        return _SHOW_TEMPLATE[phase], _PURPOSE[phase]
    return _TEMPLATE[category], _PURPOSE[category]


def _gist(category: str, title: str, caption: str | None) -> str:
    if caption:
        opening = caption.strip().splitlines()[0].strip()
        return (
            f'{title}. The client has written the caption; it opens "{opening}" — '
            "compose the on-image headline and supporting line to match its message."
        )
    if category == "tradeshow":
        phase = show_phase(title)
        beat = {
            "teaser": "invite buyers to meet the Globex team at the show",
            "live": "day-one presence at the show, meeting customers and partners",
            "recap": "thank the visitors, partners and organisers after the show",
        }[phase]
        return f"{title}: {beat}."
    if category == "milestone":
        return f"{title}: celebrate the years of trusted partnership and growth."
    return f"{title}: mark the occasion in Globex's voice for a global food-trade audience."


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def read_workbook(path: Path) -> tuple[list[ImportedEntry], list[str]]:
    """Parse the workbook: (entries in sheet order, client questions)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET]
    entries: list[ImportedEntry] = []
    questions: list[str] = []
    first: date | None = None
    header_seen = False
    for row in ws.iter_rows(values_only=True):
        cells = list(row) + [None] * (4 - len(row))
        raw_date, raw_cat, raw_title, raw_caption = cells[:4]
        if not header_seen:
            header_seen = str(raw_date or "").strip().lower() == "date"
            continue
        if not any(c not in (None, "") for c in cells[:4]):
            continue
        when = _to_date(raw_date)
        if when is None:
            # A dated row is a post; anything else in the grid is a note to us.
            text = " ".join(str(c).strip() for c in cells[:4] if c not in (None, ""))
            questions.append(text)
            continue
        category = _CATEGORY.get(str(raw_cat or "").strip().lower())
        if category is None:
            log.warning("unknown calendar category; treating as special", extra={"raw": raw_cat})
            category = "special"
        title = " ".join(str(raw_title or "").split())
        caption = str(raw_caption).strip() if raw_caption not in (None, "") else None
        first = first or when
        template, purpose = _template_for(category, title)
        entries.append(
            ImportedEntry(
                seq=len(entries),
                week=((when - first).days // 7) + 1,
                planned_date=when.isoformat(),
                category=category,
                title=title,
                gist=_gist(category, title, caption),
                template=template,
                purpose=purpose,
                anchored=True,
                exact_caption=caption,
            )
        )
    return entries, questions


def write_calendar_json(
    entries: list[ImportedEntry], questions: list[str], out: Path, *, source: str
) -> None:
    doc = {
        "_meta": {
            "source": source,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(entries),
            "client_questions": questions,
            "note": (
                "Template/gist/purpose derived from the client's category and title "
                "(see app/db/calendar_import.py); exact_caption is verbatim from the sheet."
            ),
        },
        "posts": [asdict(e) for e in entries],
    }
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("calendar written", extra={"path": str(out), "entries": len(entries)})
