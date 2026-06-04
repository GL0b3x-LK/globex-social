"""The approval loop: approve (→ publish), edit (→ re-render new preview), cancel.

Each handler reads the pending draft off the conversation row (current_post_id +
the stored GeneratedPost in context) so it survives restarts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai import editor, image_gen
from app.ai.generator import GeneratedPost
from app.db import approvals, posts, storage
from app.logging_config import get_logger
from app.messaging import conversation, twilio_client
from app.messaging.conversation import ConversationState
from app.publishing import publisher
from app.workflows import messages, render_pipeline

log = get_logger("app.workflows.approval")
Row = dict[str, Any]


async def handle_approval(phone: str, convo: Row) -> None:
    post_id = convo.get("current_post_id")
    if not post_id:
        await twilio_client.send_text(phone, messages.NOTHING_PENDING)
        return
    await asyncio.to_thread(posts.set_status, post_id, "approved")
    await asyncio.to_thread(approvals.record, post_id, "approved")
    await publisher.publish_post(post_id)  # Phase 5 performs the real multi-platform publish
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.send_text(
        phone, "✅ Approved. Publishing to Instagram, Facebook, and LinkedIn."
    )
    log.info("post approved", extra={"post_id": post_id})


async def handle_edit_request(phone: str, convo: Row, feedback: str) -> None:
    post_id = convo.get("current_post_id")
    context = convo.get("context") or {}
    stored = context.get("generated")
    if not post_id or not stored:
        await twilio_client.send_text(phone, messages.NOTHING_PENDING)
        return

    current = GeneratedPost(**stored)
    if context.get("treatment") == "generated_image":
        await _edit_generated_image(phone, post_id, current, feedback, context)
        return

    await conversation.transition(phone, state=ConversationState.EDITING)
    revised = await editor.apply_edit(
        current, feedback, context={"request": context.get("request")}
    )

    await asyncio.to_thread(
        posts.update,
        post_id,
        caption=revised.caption,
        hashtags=revised.hashtags,
        template_type=revised.template_variant,
    )
    await asyncio.to_thread(approvals.record, post_id, "edit_requested", feedback)
    # NOTE: for a user-attached photo this re-render does not re-apply it (not persisted);
    # copy/layout edits are the common case. Generated-image posts DO keep their image
    # (handled in _edit_generated_image).
    image_url = await render_pipeline.render_and_store(post_id, revised)
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await twilio_client.send_media(phone, messages.preview_caption(revised), image_url)
    log.info("edit applied", extra={"post_id": post_id})


async def _edit_generated_image(
    phone: str, post_id: str, current: GeneratedPost, feedback: str, context: Row
) -> None:
    """Edit a generated-image post: a visual change regenerates the picture (img2img);
    a textual change re-renders the overlay on the SAME picture."""
    raw_url = context.get("raw_image_url")
    await conversation.transition(phone, state=ConversationState.EDITING)
    kind = await editor.classify_edit_kind(feedback)

    if kind == "visual" and raw_url:
        await twilio_client.send_text(phone, messages.REGENERATING_IMAGE)
        result = await image_gen.edit(str(raw_url), feedback)
        if not result.ok or not result.image_bytes:
            await twilio_client.send_text(phone, messages.IMAGE_EDIT_FAILED)
            await conversation.transition(phone, state=ConversationState.AWAITING_APPROVAL)
            return
        new_raw_url = await storage.upload_png(post_id, result.image_bytes, suffix="-raw")
        await asyncio.to_thread(approvals.record, post_id, "edit_requested", feedback)
        image_url = await render_pipeline.render_and_store(
            post_id, current, photo_bytes=result.image_bytes, photo_media_type="image/png"
        )
        await asyncio.to_thread(posts.set_image_url, post_id, image_url)
        await conversation.transition(
            phone,
            state=ConversationState.AWAITING_APPROVAL,
            context_patch={"raw_image_url": new_raw_url},
        )
        await twilio_client.send_media(phone, messages.preview_caption(current), image_url)
        log.info("image edit applied", extra={"post_id": post_id})
        return

    # Textual edit (or visual with no raw image to transform): re-apply the copy and
    # re-render the overlay on the SAME generated image so the picture is preserved.
    revised = await editor.apply_edit(
        current, feedback, context={"request": context.get("request")}
    )
    photo_bytes: bytes | None = None
    if raw_url:
        try:
            photo_bytes = await image_gen.download(str(raw_url))
        except Exception as exc:  # noqa: BLE001 — keep the edit working even if refetch fails
            log.error("could not refetch raw image for re-overlay", extra={"error": str(exc)})
    await asyncio.to_thread(
        posts.update,
        post_id,
        caption=revised.caption,
        hashtags=revised.hashtags,
        template_type=revised.template_variant,
    )
    await asyncio.to_thread(approvals.record, post_id, "edit_requested", feedback)
    image_url = await render_pipeline.render_and_store(
        post_id, revised, photo_bytes=photo_bytes, photo_media_type="image/png"
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await twilio_client.send_media(phone, messages.preview_caption(revised), image_url)
    log.info("text edit applied to generated-image post", extra={"post_id": post_id})


async def handle_cancellation(phone: str, convo: Row) -> None:
    post_id = convo.get("current_post_id")
    if post_id:
        await asyncio.to_thread(posts.set_status, post_id, "cancelled")
        await asyncio.to_thread(approvals.record, post_id, "cancelled")
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.send_text(phone, "👍 Cancelled. Tell me when you want a new post.")
    log.info("post cancelled", extra={"post_id": post_id})
