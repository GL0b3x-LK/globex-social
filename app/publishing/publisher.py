"""Publishing entry point: publish an approved post to its target platforms.

Called by the approval flow after Karen approves. Publishes only to platforms that
are both TARGETED (default: all) and CONNECTED to Blotato; records a per-platform
result row; marks the post `published` if at least one platform succeeded (else it
stays `approved` for the retry job). Returns the per-platform results so the caller
can tell Karen exactly where it went.
"""

from __future__ import annotations

import asyncio

from app.db import post_platforms, posts
from app.logging_config import get_logger
from app.publishing import platforms as plat
from app.publishing.blotato import PublishResult, blotato

log = get_logger("app.publishing")


async def publish_post(post_id: str) -> dict[plat.Platform, PublishResult]:
    """Publish to the post's target platforms; record results; update status."""
    post = await asyncio.to_thread(posts.get, post_id)
    if not post:
        log.error("publish requested for unknown post", extra={"post_id": post_id})
        return {}

    targets = plat.normalize(post.get("target_platforms"))
    media_url = post.get("image_url")  # holds the rendered image OR the VHS mp4 URL
    results = await blotato.publish(
        media_url, post.get("caption") or "", post.get("hashtags") or [], targets
    )

    any_ok = False
    for platform, res in results.items():
        any_ok = any_ok or res.success
        await asyncio.to_thread(
            post_platforms.record,
            str(post_id),
            platform.value,
            status="published" if res.success else "failed",
            external_id=res.url,
            error_message=res.error,
        )
    # Only flip to published if something actually went out; otherwise leave it
    # `approved` so the Phase 6 retry job can pick it up.
    if any_ok:
        await asyncio.to_thread(posts.set_status, str(post_id), "published")
    log.info(
        "publish complete",
        extra={"post_id": post_id, "ok": [p.value for p, r in results.items() if r.success]},
    )
    return results
