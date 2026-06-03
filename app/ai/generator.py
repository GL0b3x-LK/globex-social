"""Content generation: Karen's request (+ optional photo) -> on-brand GeneratedPost."""
from __future__ import annotations

import base64
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.ai.client import generate_structured
from app.ai.prompts import (
    announcement,
    branded_packaging,
    custom,
    founding_anniversary,
    holiday,
    milestone,
    product_spotlight,
    promotional,
    stats,
    trade_show,
)
from app.ai.prompts.brand import BRAND_BLOCK


class ContentCategory(StrEnum):
    trade_show = "trade_show"
    holiday = "holiday"
    milestone = "milestone"
    founding_anniversary = "founding_anniversary"
    stats = "stats"
    announcement = "announcement"
    product_spotlight = "product_spotlight"
    promotional = "promotional"
    branded_packaging = "branded_packaging"
    custom = "custom"


_PROMPTS: dict[ContentCategory, str] = {
    ContentCategory.trade_show: trade_show.TRADE_SHOW_PROMPT,
    ContentCategory.holiday: holiday.HOLIDAY_PROMPT,
    ContentCategory.milestone: milestone.MILESTONE_PROMPT,
    ContentCategory.founding_anniversary: founding_anniversary.FOUNDING_ANNIVERSARY_PROMPT,
    ContentCategory.stats: stats.STATS_PROMPT,
    ContentCategory.announcement: announcement.ANNOUNCEMENT_PROMPT,
    ContentCategory.product_spotlight: product_spotlight.PRODUCT_SPOTLIGHT_PROMPT,
    ContentCategory.promotional: promotional.PROMOTIONAL_PROMPT,
    ContentCategory.branded_packaging: branded_packaging.BRANDED_PACKAGING_PROMPT,
    ContentCategory.custom: custom.CUSTOM_PROMPT,
}


class GeneratedPost(BaseModel):
    caption: str = Field(description="The finished, ready-to-post caption text.")
    hashtags: list[str] = Field(description="3-6 specific, on-brand hashtags, each starting with '#'.")
    template_variant: str = Field(description="Which HTML template variant to render (e.g. trade_show_pre, holiday, stats).")
    rationale: str = Field(description="One sentence: why this post fits the brief and brand.")


def system_for(category: ContentCategory) -> str:
    """Compose the full system prompt: brand truth + category instructions."""
    return f"{BRAND_BLOCK}\n\n---\n\n{_PROMPTS[category]}"


def format_context(context: dict[str, Any] | None) -> str:
    if not context:
        return "(no additional context provided)"
    return "\n".join(f"- {key}: {value}" for key, value in context.items())


def _user_content(
    context: dict[str, Any] | None,
    user_message: str | None,
    image_bytes: bytes | None,
    image_media_type: str,
) -> str | list[dict[str, Any]]:
    text = (
        f"Karen's request: {user_message or '(none — generate from the context below)'}\n\n"
        f"Context:\n{format_context(context)}"
    )
    if image_bytes is None:
        return text
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    return [
        {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": encoded}},
        {"type": "text", "text": text},
    ]


async def generate_post(
    category: ContentCategory,
    context: dict[str, Any] | None,
    user_message: str | None,
    image_bytes: bytes | None = None,
    image_media_type: str = "image/jpeg",
) -> GeneratedPost:
    return await generate_structured(
        system=system_for(category),
        user_content=_user_content(context, user_message, image_bytes, image_media_type),
        output_model=GeneratedPost,
        tool_name="emit_post",
        tool_description="Emit the finished social-media post: caption, hashtags, template variant, and a one-line rationale.",
        max_tokens=1500,
    )
