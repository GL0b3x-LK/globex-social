"""Approval-history query helpers (append-only audit)."""
from __future__ import annotations

from typing import Any

from app.db.client import get_supabase, row, rows

Row = dict[str, Any]
_TABLE = "approval_history"


def record(post_id: str, action: str, feedback: str | None = None) -> Row:
    payload: Row = {"post_id": post_id, "action": action, "feedback": feedback}
    return row(get_supabase().table(_TABLE).insert(payload).execute())


def history(post_id: str) -> list[Row]:
    return rows(
        get_supabase()
        .table(_TABLE)
        .select("*")
        .eq("post_id", post_id)
        .order("created_at")
        .execute()
    )
