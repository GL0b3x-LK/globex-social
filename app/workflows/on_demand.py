"""Inbound-message handler: authorize → classify intent → route → dispatch.

Runs in a FastAPI background task (the webhook has already acked Twilio), so it can
take its time generating + rendering before sending the preview.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai import generator, image_gen, visual_planner
from app.ai import intent as ai_intent
from app.ai.generator import GeneratedPost
from app.ai.intent import Intent, IntentType
from app.config import get_settings
from app.db import approvals, posts, storage
from app.logging_config import get_logger
from app.messaging import conversation, media, transcription, twilio_client
from app.messaging.conversation import ConversationState
from app.messaging.state_machine import Action, route
from app.messaging.transcription import Outcome
from app.workflows import approval, messages, render_pipeline

log = get_logger("app.workflows.on_demand")

# (url, content_type) pairs, as the webhook now hands them over.
Media = tuple[str, str]

_VOICE_FAILURE_MESSAGE = {
    Outcome.no_speech: messages.VOICE_NO_SPEECH,
    Outcome.too_large: messages.VOICE_TOO_LONG,
    Outcome.failed: messages.VOICE_FAILED,
    Outcome.unavailable: messages.VOICE_FAILED,
}


def _is_authorized(phone: str) -> bool:
    norm = phone.strip().lower().replace(" ", "")
    return norm in set(get_settings().authorized_numbers_list)


async def handle_incoming_message(from_phone: str, body: str, media: list[Media]) -> None:
    if not _is_authorized(from_phone):
        log.warning("ignoring message from unauthorized sender", extra={"from": from_phone})
        return

    # Split attachments by kind: a voice note becomes the instruction text; a photo
    # is something to render the post on. Both can arrive in one message.
    audio = [m for m in media if m[1].startswith("audio/")]
    photo = next((m for m in media if m[1].startswith("image/")), None)

    if audio:
        transcript = await _transcribe_voice(from_phone, audio[0])
        if transcript is None:
            return  # a failure reply was already sent; nothing to route
        body = transcript  # feed the transcript through the normal text pipeline

    convo = await conversation.get_or_create(from_phone)
    state = conversation.state_of(convo)

    # If we previously asked "designed graphic or generated image?", this message is
    # the answer — resolve it and build the post, rather than re-classifying intent.
    pending = (convo.get("context") or {}).get("pending_request")
    if state is ConversationState.AWAITING_CLARIFICATION and pending:
        await _resolve_visual_clarification(from_phone, str(pending), body)
        return

    intent = await ai_intent.classify_intent(body, state.value)
    # A photo with no clear text is still a request to build a post from it — don't
    # let an image-only message fall through to "clarify".
    if photo and intent.type in (IntentType.greeting, IntentType.unclear):
        intent = Intent(
            type=IntentType.new_post_request,
            extracted_request=body or None,
            confidence=intent.confidence,
        )
    action = route(state, intent.type)
    log.info(
        "routing message",
        extra={"state": str(state), "intent": str(intent.type), "action": str(action)},
    )

    if action is Action.GENERATE:
        await _generate_and_preview(from_phone, intent.extracted_request or body, photo)
    elif action is Action.APPROVE:
        await approval.handle_approval(from_phone, convo)
    elif action is Action.EDIT:
        await approval.handle_edit_request(from_phone, convo, intent.edit_feedback or body)
    elif action is Action.CANCEL:
        await approval.handle_cancellation(from_phone, convo)
    elif action is Action.GREET:
        await twilio_client.send_text(from_phone, messages.GREETING)
    elif action is Action.NUDGE_PENDING:
        await twilio_client.send_text(from_phone, messages.NUDGE_PENDING)
    elif action is Action.NOTHING_PENDING:
        await twilio_client.send_text(from_phone, messages.NOTHING_PENDING)
    elif action is Action.CLARIFY:
        await conversation.transition(from_phone, state=ConversationState.AWAITING_CLARIFICATION)
        await twilio_client.send_text(from_phone, messages.CLARIFY)


async def _transcribe_voice(from_phone: str, audio: Media) -> str | None:
    """Download + transcribe a voice note, echoing the text back. Returns the
    transcript on success, or None after sending a friendly failure reply."""
    url, content_type = audio
    try:
        audio_bytes, content_type = await media.download_twilio_media(url)
    except Exception as exc:  # noqa: BLE001 — a fetch failure shouldn't crash the task
        log.error("voice note download failed", extra={"error": str(exc)})
        await twilio_client.send_text(from_phone, messages.VOICE_FAILED)
        return None

    result = await transcription.transcribe(audio_bytes, content_type)
    if not result.ok:
        await twilio_client.send_text(
            from_phone, _VOICE_FAILURE_MESSAGE.get(result.outcome, messages.VOICE_FAILED)
        )
        return None

    # Echo all (the chosen behaviour): show Karen what was understood, then act.
    await twilio_client.send_text(from_phone, messages.voice_heard(result.text))
    return result.text


async def _generate_and_preview(
    from_phone: str, request_text: str, photo: Media | None, *, allow_clarify: bool = True
) -> None:
    """Route a new-post request to the right visual treatment, then preview it.

    A photo keeps today's path. Text-only requests are planned: a designed
    typographic template (default), a generated image (with the brand overlay), or
    a clarifying question when it's genuinely ambiguous.
    """
    if photo:
        await _preview_with_user_photo(from_phone, request_text, photo)
        return

    plan = await visual_planner.plan_visual(request_text)
    log.info("visual plan", extra={"treatment": plan.treatment})

    if plan.treatment == "clarify" and allow_clarify:
        await conversation.transition(
            from_phone,
            state=ConversationState.AWAITING_CLARIFICATION,
            context_patch={"pending_request": request_text},
        )
        await twilio_client.send_text(
            from_phone, plan.clarification or messages.VISUAL_CLARIFY_DEFAULT
        )
        return

    if plan.treatment == "generated_image" and plan.image_prompt:
        await _preview_generated(from_phone, request_text, plan.image_prompt)
        return

    # typographic — the default, and the fallback when a re-ask stays unsure
    generated = await generator.generate_freeform(request_text)
    await _finalize_preview(from_phone, request_text, generated, treatment="typographic")


async def _resolve_visual_clarification(from_phone: str, pending_request: str, answer: str) -> None:
    """Karen answered 'designed graphic or generated image?' — resolve and build."""
    intent = await ai_intent.classify_intent(answer, ConversationState.AWAITING_CLARIFICATION.value)
    if intent.type is IntentType.cancellation:
        await conversation.transition(
            from_phone, state=ConversationState.IDLE, context_patch={"pending_request": None}
        )
        await twilio_client.send_text(
            from_phone, "👍 No problem — tell me when you'd like to make a post."
        )
        return
    combined = (
        f"{pending_request}\n\n(Karen was asked whether she wants a designed graphic or a "
        f"generated image. Her answer: {answer})"
    )
    await conversation.transition(from_phone, context_patch={"pending_request": None})
    # allow_clarify=False: don't loop — a still-ambiguous re-ask becomes a designed graphic.
    await _generate_and_preview(from_phone, combined, None, allow_clarify=False)


async def _preview_with_user_photo(from_phone: str, request_text: str, photo: Media) -> None:
    """Karen attached a photo: render it on the template (today's behaviour)."""
    photo_bytes: bytes | None = None
    photo_type = "image/jpeg"
    try:
        photo_bytes, photo_type = await media.download_twilio_media(photo[0])
    except Exception as exc:  # noqa: BLE001 — a bad photo shouldn't kill the draft
        log.error("media download failed; continuing without photo", extra={"error": str(exc)})
        photo_bytes = None
    generated = await generator.generate_freeform(
        request_text, image_bytes=photo_bytes, image_media_type=photo_type
    )
    await _finalize_preview(
        from_phone,
        request_text,
        generated,
        image_bytes=photo_bytes,
        image_media_type=photo_type,
        treatment="user_photo",
    )


async def _preview_generated(from_phone: str, request_text: str, image_prompt: str) -> None:
    """Generate an image via kie.ai, then overlay the brand template on it."""
    await twilio_client.send_text(from_phone, messages.GENERATING_IMAGE)
    result = await image_gen.generate(image_prompt)
    generated = await generator.generate_freeform(request_text)
    if not result.ok or not result.image_bytes:
        # Don't leave Karen hanging — fall back to a designed version.
        await twilio_client.send_text(from_phone, messages.IMAGE_GEN_FAILED)
        await _finalize_preview(from_phone, request_text, generated, treatment="typographic")
        return
    await _finalize_preview(
        from_phone,
        request_text,
        generated,
        image_bytes=result.image_bytes,
        image_media_type="image/png",
        raw_image_bytes=result.image_bytes,
        treatment="generated_image",
        image_prompt=image_prompt,
    )


async def _finalize_preview(
    from_phone: str,
    request_text: str,
    generated: GeneratedPost,
    *,
    image_bytes: bytes | None = None,
    image_media_type: str = "image/jpeg",
    raw_image_bytes: bytes | None = None,
    treatment: str = "typographic",
    image_prompt: str | None = None,
) -> None:
    """Create the post, render (with overlay if an image is present), store, and preview."""
    post = await asyncio.to_thread(
        posts.create,
        content=request_text,
        caption=generated.caption,
        hashtags=generated.hashtags,
        template_type=generated.template_variant,
        status="pending_approval",
    )
    post_id = post["id"]
    await asyncio.to_thread(approvals.record, post_id, "generated")

    # Persist the raw generated image so img2img edits can transform it later.
    raw_image_url: str | None = None
    if raw_image_bytes is not None:
        raw_image_url = await storage.upload_png(post_id, raw_image_bytes, suffix="-raw")

    image_url = await render_pipeline.render_and_store(
        post_id, generated, photo_bytes=image_bytes, photo_media_type=image_media_type
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)

    context_patch: dict[str, Any] = {
        "generated": generated.model_dump(),
        "request": request_text,
        "treatment": treatment,
        "pending_request": None,
    }
    if image_prompt is not None:
        context_patch["image_prompt"] = image_prompt
    if raw_image_url is not None:
        context_patch["raw_image_url"] = raw_image_url

    await conversation.transition(
        from_phone,
        state=ConversationState.AWAITING_APPROVAL,
        current_post_id=post_id,
        context_patch=context_patch,
    )
    await twilio_client.send_media(from_phone, messages.preview_caption(generated), image_url)
    log.info(
        "preview sent",
        extra={"post_id": post_id, "variant": generated.template_variant, "treatment": treatment},
    )
