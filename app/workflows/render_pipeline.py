"""Bridge the AI output to a rendered, stored image.

`build_slots` maps a GeneratedPost's display fields (+ any context-supplied slots
from the scheduler path) onto the template's Jinja slots; `render_and_store`
renders the PNG and uploads it, returning the public URL. Shared by the on-demand
new-draft and edit-re-render flows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.ai.generator import GeneratedPost
from app.db import storage
from app.logging_config import get_logger
from app.templates import renderer as render_mod
from app.templates.assets import image_data_uri
from app.templates.catalog import TEMPLATES

log = get_logger("app.workflows.render")

# Display slots the scheduler path may supply via context (the AI fills the rest).
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
)
_FALLBACK_VARIANT = "promotional"
# Templates that render an attached photo (full-bleed background, or a framed snapshot).
_PHOTO_CAPABLE = {"custom", "trade_show_during", "polaroid"}


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


def resolve_variant(template_variant: str) -> str:
    """Guard against an unknown variant from the model — fall back, don't crash."""
    if template_variant in TEMPLATES:
        return template_variant
    log.warning("unknown template_variant; falling back", extra={"variant": template_variant})
    return _FALLBACK_VARIANT


async def render_and_store(
    post_id: str | UUID,
    post: GeneratedPost,
    *,
    context: dict[str, Any] | None = None,
    photo_bytes: bytes | None = None,
    photo_media_type: str = "image/jpeg",
) -> str:
    slots = build_slots(post, context)
    variant = resolve_variant(post.template_variant)
    if photo_bytes is not None:
        slots["photo"] = image_data_uri(photo_bytes, photo_media_type)
        # A supplied photo must be used: if the model chose a text-only template
        # (e.g. "stats" for "150 ships"), switch to a photo template so the image shows.
        if variant not in _PHOTO_CAPABLE:
            log.info(
                "photo attached; using photo template", extra={"from": variant, "to": "custom"}
            )
            variant = "custom"
    png = await render_mod.renderer.render(variant, slots)
    return await storage.upload_png(post_id, png)
