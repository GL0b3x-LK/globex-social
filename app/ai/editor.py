"""Edit application: weave Karen's feedback into an existing draft and regenerate."""
from __future__ import annotations

from typing import Any

from app.ai.client import generate_structured
from app.ai.generator import (
    ContentCategory,
    GeneratedPost,
    format_context,
    freeform_system,
    system_for,
)


async def apply_edit(
    current_post: GeneratedPost,
    feedback: str,
    *,
    category: ContentCategory | None = None,
    context: dict[str, Any] | None = None,
) -> GeneratedPost:
    """Regenerate the post with Karen's requested change applied, preserving the rest."""
    user_content = (
        "Current draft:\n"
        f"- caption: {current_post.caption}\n"
        f"- hashtags: {' '.join(current_post.hashtags)}\n"
        f"- template_variant: {current_post.template_variant}\n"
        f"- headline: {current_post.headline}\n"
        f"- subhead: {current_post.subhead or ''}\n"
        f"- figure: {current_post.figure or ''}\n"
        f"- figure_unit: {current_post.figure_unit or ''}\n\n"
        f"Karen's requested change:\n{feedback}\n\n"
        f"Original context:\n{format_context(context)}\n\n"
        "Apply ONLY the requested change. Keep everything she did not ask to change "
        "(including template_variant, headline, subhead, and figure unless the change implies "
        "they should differ). Emit the full revised post."
    )
    return await generate_structured(
        system=system_for(category) if category is not None else freeform_system(),
        user_content=user_content,
        output_model=GeneratedPost,
        tool_name="emit_post",
        tool_description="Emit the revised social-media post after applying the requested change.",
        max_tokens=1500,
    )
