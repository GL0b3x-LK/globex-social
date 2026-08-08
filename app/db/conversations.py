"""Per-phone WhatsApp conversation state. Persisted so Railway restarts don't
lose Karen's in-progress draft."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.client import get_supabase, maybe_row, row

Row = dict[str, Any]
_TABLE = "conversations"
DEFAULT_STATE = "idle"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def get(phone: str) -> Row | None:
    return maybe_row(
        get_supabase().table(_TABLE).select("*").eq("phone_number", phone).limit(1).execute()
    )


def get_or_create(phone: str) -> Row:
    existing = get(phone)
    if existing:
        return existing
    payload: Row = {"phone_number": phone, "state": DEFAULT_STATE, "context": {}}
    return row(get_supabase().table(_TABLE).insert(payload).execute())


def transition(
    phone: str,
    *,
    state: str | None = None,
    current_post_id: str | None = None,
    context_patch: dict[str, Any] | None = None,
) -> Row:
    """Atomically update a conversation, merging context_patch into context.

    current_post_id is only written when provided (non-None); omit it to leave
    unchanged, or call clear_post() to detach.
    """
    current = get_or_create(phone)
    fields: Row = {"updated_at": _now()}
    if state is not None:
        fields["state"] = state
    if context_patch:
        fields["context"] = {**(current.get("context") or {}), **context_patch}
    if current_post_id is not None:
        fields["current_post_id"] = current_post_id

    return row(get_supabase().table(_TABLE).update(fields).eq("phone_number", phone).execute())


def clear_post(phone: str) -> Row:
    """Detach the current draft (e.g. after publish/cancel)."""
    return row(
        get_supabase()
        .table(_TABLE)
        .update({"current_post_id": None, "updated_at": _now()})
        .eq("phone_number", phone)
        .execute()
    )


def delete(phone: str) -> None:
    get_supabase().table(_TABLE).delete().eq("phone_number", phone).execute()
