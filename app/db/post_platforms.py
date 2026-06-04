"""post_platforms: one row per (post, platform) publish attempt + result."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.client import get_supabase
from app.db.client import row as _row

Row = dict[str, Any]
_TABLE = "post_platforms"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record(
    post_id: str,
    platform: str,
    *,
    status: str,  # "published" | "failed" | "pending"
    external_id: str | None = None,
    error_message: str | None = None,
) -> Row:
    """Upsert the per-platform result (unique on post_id+platform, so retries overwrite)."""
    payload: Row = {
        "post_id": post_id,
        "platform": platform,
        "status": status,
        "external_id": external_id,
        "error_message": error_message,
    }
    if status == "published":
        payload["published_at"] = _now()
    return _row(
        get_supabase().table(_TABLE).upsert(payload, on_conflict="post_id,platform").execute()
    )
