"""Video persistence, on the existing posts table.

A video is a post whose medium happens to be video, so it lives in the same
table and inherits the machinery that already works: the approval lifecycle,
the audit history, per-platform publish results, and the hold-until-its-date
gate. The video-specific document (script, scenes, edit spec, spend) rides in
``render_meta``, which is already a jsonb column.

That choice is deliberate rather than lazy: the dedicated tables in schema.sql
are the eventual home, but they need DDL access we do not have yet, and
inventing a parallel approval path would risk the one rule that matters —
nothing publishes without approval.
"""

from __future__ import annotations

from typing import Any

from app.db import posts as posts_db
from app.db.client import Row

EVENT_TYPE = "video"

# Statuses reuse the posts lifecycle. 'draft' covers everything up to the point
# a cut exists; 'pending_approval' means the operator is looking at something.
SCRIPT_REVIEW = "draft"
VIDEO_REVIEW = "pending_approval"


def create(brief: str, requested_by: str) -> Row:
    """Open a new video, in script review."""
    row = posts_db.create(
        content=brief,
        template_type="video",
        status=SCRIPT_REVIEW,
        event_type=EVENT_TYPE,
    )
    return posts_db.set_render_meta(
        str(row["id"]),
        {"kind": "video", "brief": brief, "requested_by": requested_by, "spend": 0.0},
    )


def meta(video: Row) -> dict[str, Any]:
    return dict(video.get("render_meta") or {})


def patch_meta(video_id: str, **fields: Any) -> Row:
    """Merge fields into the video document, preserving what is already there."""
    current = meta(posts_db.get(video_id) or {})
    current.update(fields)
    return posts_db.set_render_meta(video_id, current)


def is_video(row: Row | None) -> bool:
    return bool(row) and (row or {}).get("event_type") == EVENT_TYPE


def add_spend(video_id: str, dollars: float) -> float:
    """Accumulate generation cost so the operator can always be told the total."""
    current = meta(posts_db.get(video_id) or {})
    total = round(float(current.get("spend") or 0.0) + dollars, 2)
    current["spend"] = total
    posts_db.set_render_meta(video_id, current)
    return total


def scene_artifacts(video_id: str) -> dict[str, Any]:
    """Per-scene artifacts keyed by content hash — the never-pay-twice ledger."""
    return dict(meta(posts_db.get(video_id) or {}).get("artifacts") or {})


def remember_artifact(video_id: str, content_hash: str, url: str) -> None:
    """Record a generated clip against the inputs that produced it.

    A retry, a crash, or an unrelated edit must never pay to regenerate a scene
    whose inputs have not changed.
    """
    current = meta(posts_db.get(video_id) or {})
    artifacts = dict(current.get("artifacts") or {})
    artifacts[content_hash] = url
    current["artifacts"] = artifacts
    posts_db.set_render_meta(video_id, current)


def latest_for(phone: str) -> Row | None:
    """The most recent video this operator has in play, if any."""
    for row in posts_db.recent(limit=40):
        if is_video(row) and meta(row).get("requested_by") == phone:
            if row.get("status") in (SCRIPT_REVIEW, VIDEO_REVIEW, "edit_requested"):
                return row
    return None
