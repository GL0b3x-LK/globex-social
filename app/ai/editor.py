"""Edit application: weave the operator's feedback into an existing draft.

An edit is not a fresh generation, and treating it as one was how edits went
wrong: the full generation prompt carries house style (title-case headlines,
punctuation habits, template taste), so an explicit instruction like "make the
title ALL CAPS" or "no punctuation" lost the argument with the style guide.
The edit prompt therefore ranks the operator's instruction ABOVE style, and the
only thing still above the operator is the client's contractual no-say list.

The model is also not trusted on the parts that must be mechanical:

* hashtags dictated inside a caption are MOVED to the hashtags field in code,
  or the caption + hashtags join at preview/publish time shows them twice;
* the template never changes unless the feedback actually asked for a layout
  change — the model swapped templates on a caption edit when left to choose.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from app.ai import style
from app.ai.client import generate_structured
from app.ai.generator import GeneratedPost, banned_claims, format_context
from app.logging_config import get_logger
from app.video import library

log = get_logger("app.ai.editor")

_EDIT_KIND_PROMPT = """An operator is reviewing a social post that has a BACKGROUND IMAGE (a photo or photo-style scene) with TEXT overlaid on top (a small light-blue label, a headline, a subtitle) plus a caption beside it. They asked for a change. Classify it:
- "visual" — ONLY the PICTURE changes: its subject, setting, colours, lighting, composition, or what is depicted ("make it a sunset", "add shipping crates", "show a farm instead", "make it brighter").
- "textual" — ONLY the WORDS change: caption, label, headline, subtitle, wording, tone, hashtags, or length ("shorten the headline", "make the caption punchier", "change the subtitle to X", "the blue text should say COMPANY ANNIVERSARY"). Note that recolouring or rewording ON-IMAGE TEXT is textual, not visual — only the photograph underneath counts as the picture.
- "both" — the message asks for a picture change AND a words change ("change the subtitle to X and use the photo I've attached", "make it brighter and shorten the headline").

Read the WHOLE message: a request that names new wording and also says anything about the image is "both", never just one of them. If genuinely ambiguous between visual and textual, prefer "textual" (cheaper and non-destructive)."""


class _EditKind(BaseModel):
    kind: Literal["visual", "textual", "both"]


EditKind = Literal["visual", "textual", "both"]


async def classify_edit_kind(feedback: str) -> EditKind:
    """For a post carrying a picture: does the edit touch the picture, the words, or both?

    "both" exists because a single WhatsApp line routinely asks for both at once
    ("change the subtitle to … Also add her image attached"). Forcing that into
    one bucket silently dropped whichever half lost — the operator's dictated
    subtitle went to the image model as a scene prompt and the copy never changed.
    """
    result = await generate_structured(
        system=_EDIT_KIND_PROMPT,
        user_content=f"The operator's requested change:\n{feedback}",
        output_model=_EditKind,
        tool_name="classify_edit",
        tool_description="Classify the edit as 'visual', 'textual', or 'both'.",
        max_tokens=128,
    )
    return result.kind


def _edit_system() -> str:
    banned = ", ".join(f"'{t}'" for t in library.banned_terms())
    return (
        "You are the copy editor for Globex International's social posts. An operator "
        "is reviewing a draft and has asked for a specific change. Your ONLY job is to "
        "apply that change exactly as instructed.\n\n"
        "RULES, in order:\n"
        f"1. NEVER use these phrases anywhere (client contract): {banned}. If the "
        "operator's text contains one, keep their wording otherwise intact and replace "
        "just that phrase with the closest allowed wording ('shipped globally', "
        "'Quality Control').\n"
        "2. The operator's instruction OVERRIDES every style habit. If they ask for all "
        "caps, output ALL CAPS. If they ask for no punctuation, remove the punctuation "
        "— including periods, commas, apostrophes, dashes and exclamation marks — from "
        "exactly the parts they named. If they dictate text, use their text verbatim "
        "(minus rule 1). Do not 'improve' their wording, casing or punctuation.\n"
        "3. Change ONLY what the instruction covers. Every other field — caption, "
        "eyebrow, headline, subhead, figure, hashtags, template_variant — is copied "
        "through unchanged, character for character.\n"
        "4. On-image text means eyebrow/headline/subhead/figure. The caption is the text "
        "beside the post. 'Title' or 'heading' means the headline; 'subtitle' means the "
        "subhead. The EYEBROW is the small letterspaced line ABOVE the title, rendered "
        "in light blue — operators call it the label, the kicker, the tag, the little "
        "line on top, or 'the blue text'. To take that label off the image entirely, "
        "set eyebrow to an empty string. Apply instructions to the parts they name, and "
        "only those.\n\n"
        "Field notes: hashtags belong in the hashtags field only, never inside the "
        "caption text. template_variant is the layout name and stays as it is unless "
        "the operator asked to change the layout."
    )


async def _edit_system_with_learned() -> str:
    """The edit prompt plus standing preferences. Rule order stays: no-say list,
    then TODAY'S instruction, then learned defaults — an operator asking for the
    opposite of an old rule is the operator updating it, not violating it."""
    from app.ai import learning  # local import: avoid a cycle at module load

    return _edit_system() + await learning.rules_block_async()


# Matches a hashtag token: '#' + letters/digits/underscore (WhatsApp-typical).
_HASHTAG = re.compile(r"#[A-Za-z0-9_]+")

# Words in feedback that mean the operator actually asked to change the layout.
_LAYOUT_WORDS = ("template", "layout", "design", "format", "style of the post", "look")


def split_hashtags(caption: str, existing: list[str]) -> tuple[str, list[str]]:
    """Move hashtags embedded in the caption into the hashtags list.

    The caption and the hashtags field are joined at preview and at publish, so a
    dictated caption that ends with its own hashtag line would show every tag
    twice. Lines consisting only of hashtags are removed from the caption; the
    tags merge into the list, first occurrence wins, original order kept.
    """
    found: list[str] = []
    kept_lines: list[str] = []
    for line in caption.split("\n"):
        tags = _HASHTAG.findall(line)
        remainder = _HASHTAG.sub("", line).strip(" \t·•|,-—")
        if tags and not remainder:
            found.extend(tags)  # a pure hashtag line moves out of the caption
        else:
            kept_lines.append(line)
    merged: list[str] = []
    seen: set[str] = set()
    for tag in [*existing, *found]:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            merged.append(tag)
    cleaned = "\n".join(kept_lines).strip()
    return cleaned, merged


def _pin_unrequested(revised: GeneratedPost, current: GeneratedPost, feedback: str) -> None:
    """Undo model drift on fields the operator never mentioned."""
    from app.templates.catalog import named_template  # local import: no AI->templates cycle

    # A null eyebrow from the editor means "I didn't touch it" — removing the
    # label is the empty string. Left alone, every later edit that simply omitted
    # the field would drop back to the template's standard label and quietly undo
    # a correction the operator had already made.
    if revised.eyebrow is None and current.eyebrow is not None:
        revised.eyebrow = current.eyebrow
    # A template named in the feedback is applied outright — "put this on TS-p2"
    # must not depend on the model choosing to comply (asked by name in a fresh
    # request, it didn't).
    if named := named_template(feedback):
        if revised.template_variant != named:
            log.info(
                "edit names a template; applied",
                extra={"from": revised.template_variant, "to": named},
            )
        revised.template_variant = named
        return
    if revised.template_variant != current.template_variant and not any(
        w in feedback.lower() for w in _LAYOUT_WORDS
    ):
        log.info(
            "edit tried to change template; pinned",
            extra={"from": current.template_variant, "to": revised.template_variant},
        )
        revised.template_variant = current.template_variant


def normalize(revised: GeneratedPost, current: GeneratedPost, feedback: str) -> GeneratedPost:
    """The mechanical guarantees applied after every edit, model output or not."""
    revised.caption, revised.hashtags = split_hashtags(revised.caption, revised.hashtags)
    _pin_unrequested(revised, current, feedback)
    # House capitalisation, but never over the operator: `enforce` reads the
    # feedback and stands down on the headline when they spoke about case.
    style.enforce(revised, feedback=feedback)
    return revised


async def apply_edit(
    current_post: GeneratedPost,
    feedback: str,
    *,
    category: Any | None = None,  # kept for call-site compatibility; unused
    context: dict[str, Any] | None = None,
) -> GeneratedPost:
    """Apply the operator's requested change, preserving everything else."""
    user_content = (
        "Current draft:\n"
        f"- caption: {current_post.caption}\n"
        f"- hashtags: {' '.join(current_post.hashtags)}\n"
        f"- template_variant: {current_post.template_variant}\n"
        f"- eyebrow (small light-blue label above the title): {current_post.eyebrow or ''}\n"
        f"- headline (on-image title): {current_post.headline}\n"
        f"- subhead (on-image subtitle): {current_post.subhead or ''}\n"
        f"- figure: {current_post.figure or ''}\n"
        f"- figure_unit: {current_post.figure_unit or ''}\n\n"
        f"The operator's instruction:\n{feedback}\n\n"
        f"Original context:\n{format_context(context)}\n\n"
        "Apply the instruction exactly and emit the full revised post."
    )
    system = await _edit_system_with_learned()
    revised = await generate_structured(
        system=system,
        user_content=user_content,
        output_model=GeneratedPost,
        tool_name="emit_post",
        tool_description="Emit the revised social-media post with the instruction applied.",
        max_tokens=1500,
    )
    revised = normalize(revised, current_post, feedback)

    # A check is a guarantee: the no-say list is enforced on the result, not
    # hoped for in the prompt — the operator may have dictated a struck phrase.
    if terms := banned_claims(revised):
        log.warning("edit produced forbidden claims; rewriting", extra={"terms": terms})
        revised = await generate_structured(
            system=system,
            user_content=(
                user_content
                + "\n\nYour previous revision used these forbidden phrases: "
                + ", ".join(terms)
                + ". Produce it again without them, changing nothing else."
            ),
            output_model=GeneratedPost,
            tool_name="emit_post",
            tool_description="Emit the revised post without the forbidden phrases.",
            max_tokens=1500,
        )
        revised = normalize(revised, current_post, feedback)
        if still := banned_claims(revised):
            # Surfaced, not silently shipped — the operator approves every post.
            log.error("edit still contains forbidden claims", extra={"terms": still})
    return revised
