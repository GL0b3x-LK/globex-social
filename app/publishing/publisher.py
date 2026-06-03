"""Publishing seam.

Phase 5 implements real multi-platform publishing (Blotato → IG/FB/LinkedIn) here.
Until then this is the entry point the approval flow calls when Karen approves a
post; it's intentionally a no-op-with-a-log so the rest of the pipeline is wired and
testable now. The E2E test patches this to assert approval triggers publishing.
"""

from __future__ import annotations

from app.logging_config import get_logger

log = get_logger("app.publishing")


async def publish_post(post_id: str) -> None:
    """Publish an approved post to all platforms. Stub until Phase 5."""
    log.warning(
        "publish requested but publishing is not implemented yet (Phase 5)",
        extra={"post_id": post_id},
    )
