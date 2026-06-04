"""Edit application: weave Karen's feedback into an existing draft and regenerate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from app.ai.client import generate_structured
from app.ai.generator import (
    ContentCategory,
    GeneratedPost,
    format_context,
    freeform_system,
    system_for,
)

_EDIT_KIND_PROMPT = """Karen is reviewing a social post that has a GENERATED BACKGROUND IMAGE (a photo-style scene) with TEXT overlaid on top (headline, caption). She asked for a change. Classify it:
- "visual" — the change is about the PICTURE/scene/background: its subject, setting, colours, lighting, composition, or what is depicted (e.g. "make it a sunset", "add shipping crates", "show a farm instead", "more blue tones").
- "textual" — the change is about the WORDS or layout: caption, headline, wording, tone, hashtags, or length (e.g. "shorten the headline", "make the caption punchier", "add a hashtag").
If genuinely ambiguous, prefer "textual" (cheaper and non-destructive)."""


class _EditKind(BaseModel):
    kind: Literal["visual", "textual"]


async def classify_edit_kind(feedback: str) -> Literal["visual", "textual"]:
    """For a generated-image post: is Karen's edit about the picture or the words?"""
    result = await generate_structured(
        system=_EDIT_KIND_PROMPT,
        user_content=f"Karen's requested change:\n{feedback}",
        output_model=_EditKind,
        tool_name="classify_edit",
        tool_description="Classify the edit as 'visual' (the picture) or 'textual' (the words/layout).",
        max_tokens=128,
    )
    return result.kind


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
