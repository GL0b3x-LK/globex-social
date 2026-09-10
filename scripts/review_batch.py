"""Build a whole run of calendar posts in one go, for the client review page.

    python scripts/review_batch.py --calendar app/data/calendar_2026_27.json \\
        --batch sep2026 --count 30

Runs the engine on each entry exactly as the daily 7am job would — same
prompts, same template rule, same verbatim-caption rule, same photo picker,
same renderer — and stops short of the one thing that job does next: it never
opens a chat thread or sends anything. The posts land as `draft` rows tagged
`render_meta.review_batch`, which is what /review/<batch> lists. They are
invisible to the scheduler (event_type "review", not "calendar"), to the
publish sweep (never approved) and to re-delivery (never owed).

Idempotent: an entry that already has a review post is skipped, so a crashed
run is resumed by running it again. Failures are per-entry — one bad render
is logged and the run continues.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import calendar_source, posts  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.templates.renderer import renderer  # noqa: E402
from app.workflows import scheduled  # noqa: E402
from app.workflows.on_demand import build_post  # noqa: E402

REVIEW_EVENT_TYPE = "review"
log = get_logger("review_batch")


async def build_one(entry: calendar_source.CalendarEntry, batch: str) -> str:
    composed = await scheduled.compose_calendar_post(
        entry, entry.planned_date, use_sheet_bridge=False
    )
    meta = scheduled.calendar_render_meta(entry, composed)
    meta.update(
        {
            "review_batch": batch,
            "calendar_seq": entry.seq,
            "struck_terms": composed.struck,
            "treatment": "calendar",
        }
    )
    post_id, image_url, _ = await build_post(
        scheduled._entry_brief(entry),
        composed.generated,
        image_bytes=composed.photo.read_bytes(),
        image_media_type="image/jpeg",
        treatment="calendar",
        event=(REVIEW_EVENT_TYPE, entry.event_id),
        extra_render_meta=meta,
        status="draft",
    )
    log.info(
        "built",
        extra={
            "seq": entry.seq + 1,
            "title": entry.title,
            "template": composed.generated.template_variant,
            "photo": composed.photo.name,
            "caption_locked": composed.caption_locked,
            "post_id": post_id,
            "image_url": image_url,
        },
    )
    return post_id


async def main(calendar: Path, batch: str, count: int, start: int, only: set[int]) -> int:
    configure_logging("INFO")
    entries = list(calendar_source.load_calendar_file(calendar))[start : start + count]
    if only:
        entries = [e for e in entries if e.seq + 1 in only]
    log.info(
        "review batch", extra={"batch": batch, "entries": len(entries), "calendar": str(calendar)}
    )

    await renderer.start()
    built = skipped = failed = 0
    try:
        for entry in entries:
            if await asyncio.to_thread(posts.find_for_event, entry.event_id, REVIEW_EVENT_TYPE):
                skipped += 1
                log.info(
                    "already built; skipping", extra={"seq": entry.seq + 1, "title": entry.title}
                )
                continue
            t0 = time.monotonic()
            try:
                await build_one(entry, batch)
                built += 1
            except Exception:
                failed += 1
                log.exception("entry failed", extra={"seq": entry.seq + 1, "title": entry.title})
            log.info(
                "timing", extra={"seq": entry.seq + 1, "seconds": round(time.monotonic() - t0, 1)}
            )
    finally:
        await renderer.stop()
    log.info("done", extra={"built": built, "skipped": skipped, "failed": failed})
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--calendar", type=Path, required=True)
    ap.add_argument(
        "--batch", required=True, help="slug that names the review page: /review/<batch>"
    )
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--start", type=int, default=0, help="0-based index into the calendar")
    ap.add_argument("--only", default="", help="comma-separated 1-based post numbers to (re)build")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    raise SystemExit(asyncio.run(main(a.calendar, a.batch, a.count, a.start, only)))
