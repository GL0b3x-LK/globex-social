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

    python scripts/review_batch.py --calendar ... --batch sep2026 --rerender

re-renders every post already in the batch through the current templates and
guards without touching its copy — unless that copy now fails a guard (a
headline that wraps, scaffolding in a field), in which case the post is
regenerated. This is how a rendering fix reaches posts the client is already
looking at.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import style  # noqa: E402
from app.ai.generator import GeneratedPost, headline_problem  # noqa: E402
from app.db import calendar_source, posts  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.templates.renderer import renderer  # noqa: E402
from app.workflows import render_pipeline, scheduled  # noqa: E402
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


async def _download(url: str | None) -> bytes:
    if not url:
        raise RuntimeError("post has neither a pool asset on disk nor a stored photo URL")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def rerender_one(
    row: dict[str, Any], batch: str, entries: dict[int, calendar_source.CalendarEntry]
) -> str:
    """Re-render one existing post; regenerate it if its copy fails a guard."""
    meta = dict(row.get("render_meta") or {})
    seq = int(meta.get("calendar_seq", 0))
    problem: str | None
    generated: GeneratedPost | None
    try:
        generated = GeneratedPost.model_validate(meta.get("generated") or {})
        problem = headline_problem(generated.headline)
    except ValidationError as exc:
        generated, problem = None, str(exc)[:200]
    if problem or generated is None:
        log.info("copy fails a guard; regenerating", extra={"seq": seq + 1, "problem": problem})
        await asyncio.to_thread(posts.delete, row["id"])
        await build_one(entries[seq], batch)
        return "regenerated"

    style.enforce(generated)
    photo = scheduled._POOL_DIR / str(meta.get("pool_asset") or "")
    photo_bytes = photo.read_bytes() if photo.is_file() else await _download(meta.get("photo_url"))
    image_url = await render_pipeline.render_and_store(
        row["id"], generated, photo_bytes=photo_bytes, photo_media_type="image/jpeg", fresh=True
    )
    await asyncio.to_thread(posts.set_image_url, row["id"], image_url)
    meta["generated"] = generated.model_dump()
    await asyncio.to_thread(posts.set_render_meta, row["id"], meta)
    log.info(
        "re-rendered",
        extra={"seq": seq + 1, "headline": generated.headline, "image_url": image_url},
    )
    return "rerendered"


async def main(
    calendar: Path, batch: str, count: int, start: int, only: set[int], rerender: bool
) -> int:
    configure_logging("INFO")
    all_entries = list(calendar_source.load_calendar_file(calendar))
    entries = all_entries[start : start + count]
    if only:
        entries = [e for e in entries if e.seq + 1 in only]
    by_seq = {e.seq: e for e in all_entries}

    await renderer.start()
    tally: dict[str, int] = {
        "built": 0,
        "rerendered": 0,
        "regenerated": 0,
        "skipped": 0,
        "failed": 0,
    }
    try:
        if rerender:
            rows = await asyncio.to_thread(posts.list_review_batch, batch)
            wanted = {e.seq for e in entries}
            rows = [
                r
                for r in rows
                if int((r.get("render_meta") or {}).get("calendar_seq", -1)) in wanted
            ]
            log.info("re-render", extra={"batch": batch, "posts": len(rows)})
            for row in rows:
                seq = int((row.get("render_meta") or {}).get("calendar_seq", 0))
                t0 = time.monotonic()
                try:
                    tally[await rerender_one(row, batch, by_seq)] += 1
                except Exception:
                    tally["failed"] += 1
                    log.exception("post failed", extra={"seq": seq + 1, "post_id": row["id"]})
                log.info(
                    "timing", extra={"seq": seq + 1, "seconds": round(time.monotonic() - t0, 1)}
                )
        else:
            log.info("review batch", extra={"batch": batch, "entries": len(entries)})
            for entry in entries:
                if await asyncio.to_thread(posts.find_for_event, entry.event_id, REVIEW_EVENT_TYPE):
                    tally["skipped"] += 1
                    log.info("already built; skipping", extra={"seq": entry.seq + 1})
                    continue
                t0 = time.monotonic()
                try:
                    await build_one(entry, batch)
                    tally["built"] += 1
                except Exception:
                    tally["failed"] += 1
                    log.exception(
                        "entry failed", extra={"seq": entry.seq + 1, "title": entry.title}
                    )
                log.info(
                    "timing",
                    extra={"seq": entry.seq + 1, "seconds": round(time.monotonic() - t0, 1)},
                )
    finally:
        await renderer.stop()
    log.info("done", extra=tally)
    return 1 if tally["failed"] else 0


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
    ap.add_argument(
        "--rerender",
        action="store_true",
        help="re-render existing posts through the current templates; regenerate any whose copy fails a guard",
    )
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    raise SystemExit(asyncio.run(main(a.calendar, a.batch, a.count, a.start, only, a.rerender)))
