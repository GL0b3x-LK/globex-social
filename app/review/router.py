"""The client review page: every post in a batch, with a feedback box under each.

/review/<batch>?k=<token>            the page (Karen, Len, Mike — no login)
/review/<batch>/feedback   POST      save one reviewer's verdict + note on a post
/review/<batch>/feedback.json?k=     everything they wrote, for us
/review/<batch>/feedback.md?k=       the same as a digest we can act on

Access is the link itself: a token in the query string, compared in constant
time, with a 404 (not 403) on a miss so the URL space reveals nothing. The
page is served by the app on the Railway domain because the reviewers are the
client — they must be able to open a link on a phone with nothing to sign up
for, and the rendered images already live on Supabase's public bucket.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import post_feedback, posts
from app.logging_config import get_logger

log = get_logger("app.review")

router = APIRouter(prefix="/review", tags=["review"])
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_CATEGORY_LABEL = {
    "holiday": "Holiday",
    "tradeshow": "Trade show",
    "milestone": "Milestone",
    "special": "Annual",
    "product": "Product",
    "brand": "Brand",
    "packaging": "Packaging",
}
_TEMPLATE_LABEL = {
    "ts_p1_bolddip": "Bold dip",
    "ts_p2_cut_navyborder": "Cut · navy border",
    "ts_p3_editorial": "Editorial",
    "ms_3_anniv_photo": "Anniversary",
}


def review_token() -> str:
    """The link secret. Explicit REVIEW_TOKEN if set; otherwise derived from a
    key the deployment already holds, so the page needs no new configuration."""
    settings = get_settings()
    if settings.review_token:
        return settings.review_token
    return hashlib.sha256(f"review:{settings.supabase_key}".encode()).hexdigest()[:24]


def _gate(k: str | None) -> None:
    if not k or not secrets.compare_digest(k, review_token()):
        raise HTTPException(status_code=404)


def _pretty_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso[:10]).strftime("%a %d %b %Y")
    except ValueError:
        return iso


def _view(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("render_meta") or {}
    cal = meta.get("calendar") or {}
    gen = meta.get("generated") or {}
    seq = int(meta.get("calendar_seq", cal.get("seq", 0)))
    return {
        "id": row["id"],
        "n": seq + 1,
        "title": cal.get("title") or gen.get("headline") or "Untitled",
        "date": _pretty_date(meta.get("publish_on") or cal.get("planned_date")),
        "category": _CATEGORY_LABEL.get(str(cal.get("category")), str(cal.get("category") or "")),
        "template": _TEMPLATE_LABEL.get(
            str(row.get("template_type")), str(row.get("template_type"))
        ),
        "image_url": row.get("image_url"),
        "caption": row.get("caption") or "",
        "hashtags": " ".join(row.get("hashtags") or []),
        "headline": gen.get("headline"),
        "subhead": gen.get("subhead"),
        "caption_locked": bool(meta.get("caption_locked")),
        "struck": list(meta.get("struck_terms") or []),
        "placeholder": bool(meta.get("photo_is_placeholder")),
    }


async def _load(batch: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    found = await asyncio.to_thread(posts.list_review_batch, batch)
    if not found:
        return [], []
    # The feedback store is the one piece with its own table. If it is not
    # there yet, the posts still show — reviewers can read; saving is what
    # tells them (clearly) that it is not ready, rather than the whole page
    # going down with a 500.
    try:
        notes = await asyncio.to_thread(post_feedback.list_for_batch, batch)
    except Exception as exc:  # noqa: BLE001 — degrade, don't hide the posts
        log.error("feedback store unavailable", extra={"batch": batch, "error": str(exc)[:200]})
        notes = []
    return [_view(r) for r in found], notes


@router.get("/{batch}", response_class=HTMLResponse)
async def page(request: Request, batch: str, k: str | None = Query(default=None)) -> HTMLResponse:
    _gate(k)
    items, notes = await _load(batch)
    if not items:
        raise HTTPException(status_code=404)
    return _templates.TemplateResponse(
        request,
        "review.html",
        {"batch": batch, "token": k, "posts": items, "feedback": notes},
    )


class FeedbackIn(BaseModel):
    k: str
    post_id: str | None = None  # None = a note on the batch as a whole
    author: str = Field(min_length=1, max_length=60)
    verdict: str | None = None  # "approved" | "changes" | None
    note: str | None = Field(default=None, max_length=4000)


@router.post("/{batch}/feedback")
async def save_feedback(batch: str, body: FeedbackIn) -> JSONResponse:
    _gate(body.k)
    if body.verdict not in (None, "", "approved", "changes"):
        raise HTTPException(status_code=422, detail="verdict must be approved or changes")
    if body.post_id:
        # A note can only attach to a post that is actually in this batch.
        try:
            row = await asyncio.to_thread(posts.get, body.post_id)
        except Exception as exc:  # noqa: BLE001 — same store, same clear refusal
            log.error(
                "feedback post lookup failed", extra={"batch": batch, "error": str(exc)[:200]}
            )
            raise HTTPException(status_code=503, detail="feedback store not ready") from exc
        if not row or (row.get("render_meta") or {}).get("review_batch") != batch:
            raise HTTPException(status_code=404)
    try:
        saved = await asyncio.to_thread(
            post_feedback.upsert,
            batch=batch,
            post_id=body.post_id or None,
            author=body.author.strip(),
            verdict=body.verdict or None,
            note=body.note,
        )
    except Exception as exc:  # noqa: BLE001 — a clear refusal beats a stack trace
        log.error("feedback save failed", extra={"batch": batch, "error": str(exc)[:200]})
        raise HTTPException(status_code=503, detail="feedback store not ready") from exc
    log.info(
        "review feedback saved",
        extra={
            "batch": batch,
            "post_id": body.post_id,
            "author": body.author,
            "verdict": body.verdict,
        },
    )
    return JSONResponse({"ok": True, "saved": saved})


@router.get("/{batch}/feedback.json")
async def feedback_json(batch: str, k: str | None = Query(default=None)) -> JSONResponse:
    _gate(k)
    items, notes = await _load(batch)
    return JSONResponse({"batch": batch, "posts": items, "feedback": notes})


@router.get("/{batch}/feedback.md", response_class=PlainTextResponse)
async def feedback_markdown(batch: str, k: str | None = Query(default=None)) -> PlainTextResponse:
    _gate(k)
    items, notes = await _load(batch)
    by_post: dict[str | None, list[dict[str, Any]]] = {}
    for n in notes:
        by_post.setdefault(n.get("post_id"), []).append(n)
    lines = [f"# Review feedback — {batch}", ""]
    general = by_post.get(None, [])
    if general:
        lines += ["## General", ""]
        lines += [f"- **{n['author']}**: {n.get('note') or ''}".rstrip() for n in general]
        lines.append("")
    for item in items:
        rows = by_post.get(item["id"], [])
        if not rows:
            continue
        lines += [f"## {item['n']}/{len(items)} {item['title']} — {item['date']}", ""]
        for n in rows:
            verdict = {"approved": "looks good", "changes": "needs changes"}.get(
                str(n.get("verdict")), "no verdict"
            )
            note = (n.get("note") or "").strip()
            lines.append(f"- **{n['author']}** ({verdict}){': ' + note if note else ''}")
        lines.append("")
    reviewed = {n.get("post_id") for n in notes if n.get("post_id")}
    lines.append(f"_{len(reviewed)} of {len(items)} posts have feedback._")
    return PlainTextResponse("\n".join(lines))
