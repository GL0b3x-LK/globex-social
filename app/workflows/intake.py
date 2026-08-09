"""The guided 4-question command-center flow (Aug 6 call spec).

When the operator starts a post without a real brief ("new post", "let's post
something"), the agent asks, in order: what's it about → why / what should it
achieve → picture? → which template. Answers accumulate on the conversation row
(survives restarts); a photo can arrive at any step. A substantial one-message
brief (or an attached photo) skips the questionnaire entirely — power users keep
the fast path.
"""

from __future__ import annotations

from typing import Any

from app.ai import generator
from app.ai import intent as ai_intent
from app.ai.intent import IntentType
from app.logging_config import get_logger
from app.messaging import conversation, media, twilio_client
from app.messaging.conversation import ConversationState
from app.templates.catalog import CALENDAR_TEMPLATE_ALIASES
from app.workflows import scheduled

log = get_logger("app.workflows.intake")

Media = tuple[str, str]  # (url, content_type)

# A request shorter than this (after stripping) has no usable subject — ask.
_THIN_BRIEF_CHARS = 18
# Seed the "about" answer only when the thin request still names a subject.
_SEED_MIN_CHARS = 12

Q_ABOUT = "🆕 New post — first: what's it about?"
Q_WHY = "2/4 — Why this post? What should it achieve for whoever sees it?"
Q_PHOTO = "3/4 — Picture: send a photo now, or reply *stock* and I'll use a Globex brand image."
Q_TEMPLATE = (
    "4/4 — Which template?\n"
    "1️⃣ Bold Dip — photo-first, rounded panel (booth pill optional)\n"
    "2️⃣ Clean Frame — photo in navy frame, cyan divider\n"
    "3️⃣ Editorial — headline on top, photo below\n"
    "4️⃣ Milestone — portrait + years badge\n"
    "Reply 1-4, or *auto* and I'll pick."
)
CANCELLED = "👍 No problem — post scrapped. Tell me when you'd like to start another."
BUILDING = "🛠 Got everything — building your preview now…"

_TEMPLATE_CHOICES = {
    "1": "ts_p1_bolddip",
    "2": "ts_p2_cut_navyborder",
    "3": "ts_p3_editorial",
    "4": "ms_3_anniv_photo",
}
_FINAL_VARIANTS = set(CALENDAR_TEMPLATE_ALIASES.values())

_STEPS = ("about", "why", "photo", "template")
_QUESTIONS = {"about": Q_ABOUT, "why": Q_WHY, "photo": Q_PHOTO, "template": Q_TEMPLATE}


def _next_step(data: dict[str, Any]) -> str | None:
    for step in _STEPS:
        if not data.get(step):
            return step
    return None


def is_thin_brief(request: str | None) -> bool:
    return len((request or "").strip()) < _THIN_BRIEF_CHARS


async def maybe_start(from_phone: str, request: str | None, photo: Media | None) -> bool:
    """Own the request if it needs the questionnaire; return False for the fast path."""
    if photo or not is_thin_brief(request):
        return False
    data: dict[str, Any] = {}
    text = (request or "").strip()
    if len(text) >= _SEED_MIN_CHARS:
        data["about"] = text
    step = _next_step(data)
    await conversation.transition(
        from_phone, state=ConversationState.INTAKE, context_patch={"intake": data}
    )
    await twilio_client.send_text(from_phone, _QUESTIONS[step or "about"])
    log.info("intake started", extra={"seeded": bool(data)})
    return True


async def handle_answer(
    from_phone: str, convo: dict[str, Any], body: str, photo: Media | None
) -> None:
    """Consume one inbound message while the questionnaire is active."""
    data = dict((convo.get("context") or {}).get("intake") or {})
    text = (body or "").strip()

    # An explicit "stop" at any step abandons the questionnaire.
    if text:
        intent = await ai_intent.classify_intent(text, ConversationState.INTAKE.value)
        if intent.type is IntentType.cancellation:
            await conversation.transition(
                from_phone, state=ConversationState.IDLE, context_patch={"intake": None}
            )
            await twilio_client.send_text(from_phone, CANCELLED)
            return

    # A photo attached at ANY step answers the picture question.
    if photo:
        data["photo"] = {"kind": "attached", "url": photo[0], "content_type": photo[1]}

    step = _next_step(data)
    if step in ("about", "why") and text:
        data[step] = text
    elif step == "photo" and not photo:
        if text.lower() in ("stock", "skip", "none", "no", "no photo", "auto"):
            data["photo"] = {"kind": "pool"}
        elif text:
            # They typed a description instead — treat it as art direction for the
            # pool pick (Higgsfield generation will slot in here later).
            data["photo"] = {"kind": "pool", "hint": text}
    elif step == "template" and text:
        choice = text.strip().lower().lstrip("#")
        variant = _TEMPLATE_CHOICES.get(choice[:1]) if choice[:1].isdigit() else None
        if variant is None and choice in ("auto", "you pick", "any"):
            variant = "auto"
        if variant is None:
            await twilio_client.send_text(from_phone, Q_TEMPLATE)
            return
        data["template"] = variant

    remaining = _next_step(data)
    await conversation.transition(from_phone, context_patch={"intake": data})
    if remaining:
        await twilio_client.send_text(from_phone, _QUESTIONS[remaining])
        return

    await twilio_client.send_text(from_phone, BUILDING)
    await _build(from_phone, data)


def _auto_variant(brief: str) -> str:
    b = brief.lower()
    if any(w in b for w in ("anniversary", "years with", "milestone", "work anniversary")):
        return "ms_3_anniv_photo"
    if any(w in b for w in ("booth", "trade show", "expo", "gulfood", "ippe", "stand ")):
        return "ts_p1_bolddip"
    if any(w in b for w in ("announce", "news", "launch", "partnership")):
        return "ts_p3_editorial"
    return "ts_p2_cut_navyborder"


async def _build(from_phone: str, data: dict[str, Any]) -> None:
    """All four answers in — generate, render on the chosen final, preview."""
    from app.workflows.on_demand import _finalize_preview  # local import: avoid cycle

    brief = f"{data['about']}\n\nMarketing purpose (from the operator): {data['why']}"
    generated = await generator.generate_freeform(brief)

    variant = data["template"]
    if variant == "auto":
        variant = _auto_variant(brief)
    generated.template_variant = variant

    photo_spec = data.get("photo") or {"kind": "pool"}
    if photo_spec["kind"] == "attached":
        image_bytes, media_type = await media.download_twilio_media(photo_spec["url"])
    else:
        hint = photo_spec.get("hint", "")
        path = scheduled.pick_photo_for_text(f"{brief} {hint}")
        image_bytes, media_type = path.read_bytes(), "image/jpeg"

    await conversation.transition(from_phone, context_patch={"intake": None})
    await _finalize_preview(
        from_phone,
        brief,
        generated,
        image_bytes=image_bytes,
        image_media_type=media_type,
        treatment="guided",
    )
    log.info("intake complete", extra={"variant": variant, "photo": photo_spec["kind"]})
