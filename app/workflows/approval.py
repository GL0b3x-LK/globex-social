"""The approval loop: approve (→ publish), edit (→ re-render new preview), cancel.

Each handler reads the pending draft off the conversation row (current_post_id +
the stored GeneratedPost in context) so it survives restarts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai import editor
from app.ai.generator import GeneratedPost
from app.db import approvals, posts
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
    # NOTE: re-render does not re-apply Karen's original photo (not persisted); copy/
    # layout edits are the common case. Persisting the source photo is a follow-up.
    image_url = await render_pipeline.render_and_store(post_id, revised)
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await twilio_client.send_media(phone, messages.preview_caption(revised), image_url)
    log.info("edit applied", extra={"post_id": post_id})


async def handle_cancellation(phone: str, convo: Row) -> None:
    post_id = convo.get("current_post_id")
    if post_id:
        await asyncio.to_thread(posts.set_status, post_id, "cancelled")
        await asyncio.to_thread(approvals.record, post_id, "cancelled")
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.send_text(phone, "👍 Cancelled. Tell me when you want a new post.")
    log.info("post cancelled", extra={"post_id": post_id})
