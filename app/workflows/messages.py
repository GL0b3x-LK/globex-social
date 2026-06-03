"""Canned WhatsApp replies + the preview-caption builder. No I/O, no cycles."""

from __future__ import annotations

from app.ai.generator import GeneratedPost

GREETING = (
    'Hi! Tell me what you\'d like to post — e.g. "post about us at Gulfood" or a '
    "shipment stat — and I'll draft it for your approval."
)
NUDGE_PENDING = (
    "You've got a draft waiting. Reply *approve* to publish, send an edit "
    '(e.g. "make it shorter"), or *cancel*.'
)
NOTHING_PENDING = (
    "Nothing's in progress right now. Tell me what you'd like to post and I'll draft it."
)
CLARIFY = (
    "I didn't quite catch that. Tell me what to post — a trade show, a stat, a holiday, "
    "an announcement — and I'll draft it."
)


def preview_caption(post: GeneratedPost) -> str:
    """The WhatsApp text sent alongside the preview image."""
    body = f"{post.caption}\n\n{' '.join(post.hashtags)}".strip()
    return f"{body}\n\n— Reply *approve* to publish, send an edit, or *cancel*."
