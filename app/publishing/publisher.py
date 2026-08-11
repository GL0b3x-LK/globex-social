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
from app.publishing.formatter import format_caption

log = get_logger("app.publishing")


async def _write_back_to_sheet(post: dict, results: dict[plat.Platform, PublishResult]) -> None:
    """Record the as-posted caption in the calendar sheet's "Exact Caption" cell.

    Only calendar posts have a row; the title is resolved from the post's
    event_id against the calendar itself, so it works even for posts whose
    render_meta was damaged. What is written is the exact Instagram caption
    string (caption + hashtags as joined at publish). Best-effort by contract:
    a sheet problem must never turn a successful publish into a failure.
    """
    from app.db import calendar_source
    from app.publishing import calendar_sheet

    try:
        if post.get("event_type") != calendar_source.EVENT_TYPE:
            return
        event_id = str(post.get("event_id") or "")
        entry = next((e for e in calendar_source.load_calendar() if e.event_id == event_id), None)
        if entry is None:
            return
        posted = format_caption(
            post.get("caption") or "", post.get("hashtags") or [], plat.Platform.instagram
        )
        if await calendar_sheet.write_back(entry.title, posted):
            log.info("sheet caption written back", extra={"title": entry.title})
    except Exception as exc:  # noqa: BLE001 — bookkeeping, never a publish failure
        log.warning("sheet write-back skipped", extra={"error": str(exc)[:150]})


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
        await _write_back_to_sheet(post, results)
    log.info(
        "publish complete",
        extra={"post_id": post_id, "ok": [p.value for p, r in results.items() if r.success]},
    )
    return results
