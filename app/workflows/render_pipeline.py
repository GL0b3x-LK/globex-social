"""Bridge the AI output to a rendered, stored image.

`build_slots` maps a GeneratedPost's display fields (+ any context-supplied slots
from the scheduler path) onto the template's Jinja slots; `render_and_store`
renders the PNG and uploads it, returning the public URL. Shared by the on-demand
new-draft, edit-re-render, and calendar-scheduler flows.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from app.ai.generator import GeneratedPost
from app.db import storage
from app.logging_config import get_logger
from app.templates import renderer as render_mod
from app.templates.assets import image_data_uri
from app.templates.catalog import (
    CALENDAR_TEMPLATE_ALIASES,
    DEFAULT_FINAL,
    PLATFORM_DIMENSIONS,
    TEMPLATES,
)

log = get_logger("app.workflows.render")

# Display slots the scheduler/context path may supply (the AI fills the rest).
_CONTEXT_SLOTS = (
    "eyebrow",
    "meta",
    "booth",
    "name",
    "role",
    "date_label",
    "month_label",
    "cta",
    "stat_items",
    "product_image",
    "package_image",
    "sig",
    # Final-template slots (TS-p1/p2/p3, MS-3):
    "subline_strong",
    "subline_soft",
    "pill",
    "years",
    "message",
)
# An unknown variant used to land on `promotional` — a demo-era template in
# Montserrat. Anything unrecognised now resolves to an approved one.
_FALLBACK_VARIANT = DEFAULT_FINAL


def build_slots(post: GeneratedPost, context: dict[str, Any] | None = None) -> dict[str, Any]:
    slots: dict[str, Any] = {"headline": post.headline}
    if post.subhead:
        slots["subhead"] = post.subhead
    if post.figure:
        slots["figure"] = post.figure
    if post.figure_unit:
        slots["figure_unit"] = post.figure_unit
    if context:
        for key in _CONTEXT_SLOTS:
            if context.get(key) not in (None, "", []):
                slots[key] = context[key]
    return slots


def resolve_eyebrow(
    post: GeneratedPost, variant: str, context: dict[str, Any] | None = None
) -> str:
    """The label that will actually sit above the headline, in light blue.

    Precedence: what the post carries (the AI wrote it, or an edit changed it),
    then anything the scheduler supplied, then the template's standard label.
    An empty string is a decision rather than an absence — it is how "drop the
    label" is expressed — so only ``None`` falls through to the next source.
    """
    if post.eyebrow is not None:
        return post.eyebrow.strip()
    if context and context.get("eyebrow"):
        return str(context["eyebrow"]).strip()
    return TEMPLATES[variant].eyebrow_default


def _adapt_final_slots(variant: str, slots: dict[str, Any], post: GeneratedPost) -> None:
    """Map generic AI fields onto the finals' slot names when context didn't."""
    spec = TEMPLATES[variant]
    if spec.canvas != "portrait":
        return
    # TS-p1/p2: one supporting line under the headline. A "date · City, Country"
    # subhead splits at the middot into the approved two-tone treatment.
    if "subline_strong" in spec.optional_slots and not (
        slots.get("subline_strong") or slots.get("subline_soft")
    ):
        if post.subhead:
            if "·" in post.subhead:
                strong, _, soft = post.subhead.partition("·")
                slots["subline_strong"] = strong.strip()
                slots["subline_soft"] = f"· {soft.strip()}"
            else:
                slots["subline_strong"] = post.subhead
    # TS-p3: meta is a list of segments; a plain subhead becomes a single segment.
    if "meta" in spec.optional_slots and not slots.get("meta") and post.subhead:
        slots["meta"] = [post.subhead]
    # MS-3: the on-image message defaults to the AI subhead.
    if variant == "ms_3_anniv_photo":
        slots.setdefault("name", post.headline)
        if post.subhead and not slots.get("message"):
            slots["message"] = post.subhead


def resolve_variant(template_variant: str) -> str:
    """Guard against an unknown variant from the model — fall back, don't crash."""
    if template_variant in CALENDAR_TEMPLATE_ALIASES:
        return CALENDAR_TEMPLATE_ALIASES[template_variant]
    if template_variant in TEMPLATES:
        return template_variant
    log.warning("unknown template_variant; falling back", extra={"variant": template_variant})
    return _FALLBACK_VARIANT


async def _photo_for(post: GeneratedPost, variant: str) -> tuple[bytes, str] | None:
    """A picture for a photo template that arrived without one.

    All four approved templates are built around a photograph, so a from-scratch
    post that reaches one with no image would render its photo panel empty. The
    bank exists precisely so that never has to happen: a real Globex photograph
    matched to what the post is about beats an empty frame, and beats dropping
    back to a demo-era template in the wrong typeface.
    """
    from app.workflows import scheduled  # local import: scheduled reaches back here

    try:
        path = await asyncio.to_thread(
            scheduled.pick_photo_for_text,
            f"{post.headline} {post.subhead or ''} {post.caption}",
            "brand",
            exclude=await asyncio.to_thread(scheduled.recently_used),
        )
        return await asyncio.to_thread(path.read_bytes), "image/jpeg"
    except Exception as exc:  # noqa: BLE001 — an empty frame is worse than a plain render
        log.error("could not take a photo from the bank", extra={"error": str(exc)[:200]})
        return None


async def render_and_store(
    post_id: str | UUID,
    post: GeneratedPost,
    *,
    context: dict[str, Any] | None = None,
    photo_bytes: bytes | None = None,
    photo_media_type: str = "image/jpeg",
    fresh: bool = False,
) -> str:
    """Render the post and store the PNG; returns its public URL.

    ``fresh`` writes to a NEW object instead of overwriting ``{post_id}.png``.
    Rendered posts are served with an hour of CDN cache, so an edit that reuses
    the same key can hand the operator back the pre-edit image and read as "my
    change did nothing". Every re-render therefore gets its own key, which also
    leaves the previous version in place to walk an edit back.
    """
    slots = build_slots(post, context)
    variant = resolve_variant(post.template_variant)
    if photo_bytes is not None and not TEMPLATES[variant].needs_photo:
        # A supplied photo must be used: if the model chose a text-only template
        # (e.g. "stats" for "150 ships"), switch to a photo template so the image
        # shows. That switch used to land on `custom`, a demo-era template in the
        # wrong typeface; it lands on an approved one now.
        log.info(
            "photo attached; using photo template",
            extra={"from": variant, "to": DEFAULT_FINAL},
        )
        variant = DEFAULT_FINAL
    if photo_bytes is None and TEMPLATES[variant].needs_photo:
        # The approved four are all photo templates, so a post that picked one
        # without an image needs a picture rather than a different template.
        if taken := await _photo_for(post, variant):
            photo_bytes, photo_media_type = taken
    if photo_bytes is not None:
        slots["photo"] = image_data_uri(photo_bytes, photo_media_type)
    # Resolved only once the variant is final (the template supplies the default
    # label) and stamped back onto the post, so the stored record says what the
    # picture says. Edits read that value: a label the code cannot see is a label
    # nobody can change, which is how a company birthday kept its "WORK
    # ANNIVERSARY" through every correction. A template with no eyebrow slot is
    # stamped blank rather than left carrying copy that never reaches the image.
    spec = TEMPLATES[variant]
    post.eyebrow = resolve_eyebrow(post, variant, context) if "eyebrow" in spec.all_slots() else ""
    slots["eyebrow"] = post.eyebrow
    _adapt_final_slots(variant, slots, post)
    dimensions = PLATFORM_DIMENSIONS[TEMPLATES[variant].canvas]
    png = await render_mod.renderer.render(variant, slots, dimensions=dimensions)
    suffix = f"-r{uuid4().hex[:6]}" if fresh else ""
    return await storage.upload_png(post_id, png, suffix=suffix)
