"""Inbound-message handler: authorize → classify intent → route → dispatch.

Runs in a FastAPI background task (the webhook has already acked Twilio), so it can
take its time generating + rendering before sending the preview.
"""

from __future__ import annotations

import asyncio

from app.ai import generator
from app.ai import intent as ai_intent
from app.config import get_settings
from app.db import approvals, posts
from app.logging_config import get_logger
from app.messaging import conversation, media, twilio_client
from app.messaging.conversation import ConversationState
from app.messaging.state_machine import Action, route
from app.workflows import approval, messages, render_pipeline

log = get_logger("app.workflows.on_demand")


def _is_authorized(phone: str) -> bool:
    norm = phone.strip().lower().replace(" ", "")
    return norm in set(get_settings().authorized_numbers_list)


async def handle_incoming_message(from_phone: str, body: str, media_urls: list[str]) -> None:
    if not _is_authorized(from_phone):
        log.warning("ignoring message from unauthorized sender", extra={"from": from_phone})
        return

    convo = await conversation.get_or_create(from_phone)
    state = conversation.state_of(convo)
    intent = await ai_intent.classify_intent(body, state.value)
    action = route(state, intent.type)
    log.info(
        "routing message",
        extra={"state": str(state), "intent": str(intent.type), "action": str(action)},
    )

    if action is Action.GENERATE:
        await _generate_and_preview(from_phone, intent.extracted_request or body, media_urls)
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


async def _generate_and_preview(from_phone: str, request_text: str, media_urls: list[str]) -> None:
    photo_bytes: bytes | None = None
    photo_type = "image/jpeg"
    if media_urls:
        try:
            photo_bytes, photo_type = await media.download_twilio_media(media_urls[0])
        except Exception as exc:  # noqa: BLE001 — a bad photo shouldn't kill the draft
            log.error("media download failed; continuing without photo", extra={"error": str(exc)})
            photo_bytes = None

    generated = await generator.generate_freeform(
        request_text, image_bytes=photo_bytes, image_media_type=photo_type
    )
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

    image_url = await render_pipeline.render_and_store(
        post_id, generated, photo_bytes=photo_bytes, photo_media_type=photo_type
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await conversation.transition(
        from_phone,
        state=ConversationState.AWAITING_APPROVAL,
        current_post_id=post_id,
        context_patch={"generated": generated.model_dump(), "request": request_text},
    )
    await twilio_client.send_media(from_phone, messages.preview_caption(generated), image_url)
    log.info("preview sent", extra={"post_id": post_id, "variant": generated.template_variant})
