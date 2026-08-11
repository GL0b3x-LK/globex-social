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
    freeform,
    holiday,
    milestone,
    product_spotlight,
    promotional,
    stats,
    trade_show,
)
from app.ai.prompts.brand import BRAND_BLOCK
from app.logging_config import get_logger


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


log = get_logger("app.ai.generator")


class GeneratedPost(BaseModel):
    caption: str = Field(description="The finished, ready-to-post caption text (the post body).")
    hashtags: list[str] = Field(
        description="3-6 specific, on-brand hashtags, each starting with '#'."
    )
    template_variant: str = Field(
        description="Which HTML template variant to render (e.g. trade_show_pre, holiday, stats)."
    )
    headline: str = Field(
        description="Short on-image headline, <= 6 words — the graphic's main line. NOT the caption."
    )
    subhead: str | None = Field(
        default=None,
        description="Optional one-line on-image supporting text, <= 14 words. Null if not needed.",
    )
    figure: str | None = Field(
        default=None,
        description="Number-led posts only: the hero figure exactly as shown (e.g. '150', '33', '90+'). Null otherwise.",
    )
    figure_unit: str | None = Field(
        default=None, description="Short label beside the figure (e.g. 'Years'). Null otherwise."
    )
    rationale: str = Field(description="One sentence: why this post fits the brief and brand.")


_EMIT_DESC = (
    "Emit the finished post: caption, hashtags, template_variant, the on-image headline/subhead "
    "(plus figure/figure_unit for number-led posts), and a one-line rationale."
)


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
        {
            "type": "image",
            "source": {"type": "base64", "media_type": image_media_type, "data": encoded},
        },
        {"type": "text", "text": text},
    ]


def banned_claims(post: GeneratedPost) -> list[str]:
    """Forbidden claims anywhere the reader will see them.

    'Halal', '90+ countries' and 'inspected by hand' were struck by the client.
    The prompts no longer ask for them, but a prompt is a request and a check is
    a guarantee — the model volunteered "halal on request" and "90+ countries"
    into the first scheduled caption entirely on its own.
    """
    from app.video import library  # local import: keeps the AI layer standalone

    visible = " ".join(
        part for part in (post.caption, post.headline, post.subhead, *post.hashtags) if part
    )
    return library.banned_terms_in(visible)


async def _repair_claims(
    post: GeneratedPost, system: str, user_content: Any, terms: list[str]
) -> GeneratedPost:
    """Rewrite a post that used a struck phrase, naming the phrase."""
    log.warning("post used forbidden claims; rewriting", extra={"terms": terms})
    return await generate_structured(
        system=system,
        user_content=[
            {"type": "text", "text": str(user_content) if isinstance(user_content, str) else ""},
            {
                "type": "text",
                "text": (
                    "Your previous draft used phrases the client has struck: "
                    + ", ".join(f"'{t}'" for t in terms)
                    + ".\nWrite the post again without any of them. Say 'shipped globally' "
                    "instead of naming a number of countries, and 'Quality Control' instead "
                    "of describing hand inspection. Never mention Halal."
                ),
            },
        ],
        output_model=GeneratedPost,
        tool_name="emit_post",
        tool_description=_EMIT_DESC,
        max_tokens=1500,
    )


async def generate_post(
    category: ContentCategory,
    context: dict[str, Any] | None,
    user_message: str | None,
    image_bytes: bytes | None = None,
    image_media_type: str = "image/jpeg",
) -> GeneratedPost:
    from app.ai import learning  # local import: avoid a cycle at module load

    system = system_for(category) + await learning.rules_block_async()
    user_content = _user_content(context, user_message, image_bytes, image_media_type)
    post = await generate_structured(
        system=system,
        user_content=user_content,
        output_model=GeneratedPost,
        tool_name="emit_post",
        tool_description=_EMIT_DESC,
        max_tokens=1500,
    )
    terms = banned_claims(post)
    if terms:
        post = await _repair_claims(post, system, user_message or "", terms)
        if still := banned_claims(post):
            # Surfaced rather than silently shipped: the operator still approves
            # every post, and a caption they can see is one they can reject.
            log.error("post still contains forbidden claims after rewrite", extra={"terms": still})
    return post


def freeform_system() -> str:
    """System prompt for the on-demand path: brand truth + free-form selection guidance."""
    return f"{BRAND_BLOCK}\n\n---\n\n{freeform.FREEFORM_PROMPT}"


def _with_memory(
    content: str | list[dict[str, Any]], memory: str | None
) -> str | list[dict[str, Any]]:
    """Prepend the conversation-memory block so generation can resolve references."""
    if not memory:
        return content
    if isinstance(content, str):
        return f"{memory}\n\n{content}"
    return [{"type": "text", "text": memory}, *content]


async def generate_freeform(
    request: str,
    image_bytes: bytes | None = None,
    image_media_type: str = "image/jpeg",
    memory: str | None = None,
) -> GeneratedPost:
    """On-demand path: the model selects the template_variant itself, then writes the post."""
    from app.ai import learning  # local import: avoid a cycle at module load

    system = freeform_system() + await learning.rules_block_async()
    content = _with_memory(_user_content(None, request, image_bytes, image_media_type), memory)
    post = await generate_structured(
        system=system,
        user_content=content,
        output_model=GeneratedPost,
        tool_name="emit_post",
        tool_description=_EMIT_DESC,
        max_tokens=1500,
    )
    # The struck phrases apply to anything the client publishes, not just the
    # calendar — a post asked for over WhatsApp reaches the same audience.
    if terms := banned_claims(post):
        post = await _repair_claims(post, system, request, terms)
        if still := banned_claims(post):
            log.error("post still contains forbidden claims after rewrite", extra={"terms": still})
    return post
