"""Message-log query helpers — the full WhatsApp transcript.

Powers persistent conversation memory (recent window + rolling summary) and
swipe-to-reply (map a Twilio reply SID back to the post it concerned).
"""

from __future__ import annotations

from typing import Any

from app.db.client import get_supabase, maybe_row, rows
from app.db.client import row as _row

Row = dict[str, Any]
_TABLE = "messages"


def create(
    *,
    phone_number: str,
    role: str,  # "karen" | "agent"
    body: str | None = None,
    twilio_sid: str | None = None,
    media_url: str | None = None,
    kind: str = "text",  # text | voice | image | preview
    post_id: str | None = None,
) -> Row:
    payload: Row = {
        "phone_number": phone_number,
        "role": role,
        "body": body,
        "twilio_sid": twilio_sid,
        "media_url": media_url,
        "kind": kind,
        "post_id": post_id,
    }
    return _row(get_supabase().table(_TABLE).insert(payload).execute())


def recent(phone_number: str, limit: int = 25) -> list[Row]:
    """The newest `limit` messages, returned oldest-first (chronological for prompts)."""
    resp = (
        get_supabase()
        .table(_TABLE)
        .select("*")
        .eq("phone_number", phone_number)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(rows(resp)))


def by_sid(twilio_sid: str) -> Row | None:
    """Resolve a Twilio message SID (e.g. an OriginalRepliedMessageSid) to its row."""
    return maybe_row(
        get_supabase().table(_TABLE).select("*").eq("twilio_sid", twilio_sid).limit(1).execute()
    )


def count(phone_number: str) -> int:
    resp = (
        get_supabase()
        .table(_TABLE)
        .select("id", count="exact")  # type: ignore[arg-type]  # postgrest accepts the literal
        .eq("phone_number", phone_number)
        .execute()
    )
    return resp.count or 0


def page(phone_number: str, offset: int, limit: int) -> list[Row]:
    """Chronological slice [offset, offset+limit) — used to fold older messages into the summary."""
    if limit <= 0:
        return []
    resp = (
        get_supabase()
        .table(_TABLE)
        .select("*")
        .eq("phone_number", phone_number)
        .order("created_at", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return rows(resp)
