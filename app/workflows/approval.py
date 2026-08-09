"""The approval loop: approve (→ publish), edit (→ re-render new preview), cancel.

Each handler reads the pending draft off the conversation row (current_post_id +
the stored GeneratedPost in context) so it survives restarts.
"""

from __future__ import annotations

import asyncio
from datetime import date as _date
from typing import Any

from app.ai import editor, image_gen
from app.ai.generator import GeneratedPost
from app.db import approvals, posts, storage
from app.logging_config import get_logger
from app.messaging import conversation, twilio_client
from app.messaging.conversation import ConversationState
from app.publishing import platforms as plat
from app.publishing import publisher
from app.workflows import messages, render_pipeline

log = get_logger("app.workflows.approval")
Row = dict[str, Any]


def _render_meta(
    generated: GeneratedPost,
    *,
    treatment: str,
    image_prompt: str | None = None,
    raw_image_url: str | None = None,
) -> dict[str, Any]:
    """Keep posts.render_meta in sync after an edit, so a re-opened post stays editable."""
    meta: dict[str, Any] = {"generated": generated.model_dump(), "treatment": treatment}
    if image_prompt:
        meta["image_prompt"] = image_prompt
    if raw_image_url:
        meta["raw_image_url"] = raw_image_url
    return meta


async def handle_approval(
    phone: str, convo: Row, target_platforms: list[plat.Platform] | None = None
) -> None:
    post_id = convo.get("current_post_id")
    if not post_id:
        await twilio_client.send_text(phone, messages.NOTHING_PENDING)
        return
    # Last-chance platform override at approval ("approve — just LinkedIn").
    if target_platforms:
        await asyncio.to_thread(
            posts.set_target_platforms, post_id, [p.value for p in target_platforms]
        )
    await asyncio.to_thread(posts.set_status, post_id, "approved")
    await asyncio.to_thread(approvals.record, post_id, "approved")

    # Calendar-scheduled posts hold until their date; the scheduler publishes them.
    post = await asyncio.to_thread(posts.get, post_id)
    publish_on = ((post or {}).get("render_meta") or {}).get("publish_on")
    if publish_on and _date.fromisoformat(publish_on) > _date.today():
        await conversation.transition(phone, state=ConversationState.IDLE)
        await conversation.clear_post(phone)
        pretty = _date.fromisoformat(publish_on).strftime("%A %d %B")
        await twilio_client.send_text(
            phone, f"✅ Approved — it will go out automatically on {pretty}."
        )
        log.info("post approved (scheduled)", extra={"post_id": post_id, "publish_on": publish_on})
        return

    results = await publisher.publish_post(post_id)  # real multi-platform publish via Blotato
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.send_text(phone, messages.publish_status(results))
    log.info("post approved", extra={"post_id": post_id})


async def handle_edit_request(
    phone: str,
    convo: Row,
    feedback: str,
    target_platforms: list[plat.Platform] | None = None,
) -> None:
    post_id = convo.get("current_post_id")
    context = convo.get("context") or {}
    stored = context.get("generated")
    if not post_id or not stored:
        await twilio_client.send_text(phone, messages.NOTHING_PENDING)
        return

    # Platform change mid-draft ("actually just LinkedIn") — update the target now.
    if target_platforms:
        await asyncio.to_thread(
            posts.set_target_platforms, post_id, [p.value for p in target_platforms]
        )

    current = GeneratedPost(**stored)
    if context.get("treatment") == "generated_image":
        await _edit_generated_image(phone, post_id, current, feedback, context)
        return
    if context.get("treatment") == "vhs_video":
        await _edit_vhs_caption(phone, post_id, current, feedback, context)
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
    await asyncio.to_thread(
        posts.set_render_meta,
        post_id,
        _render_meta(revised, treatment=context.get("treatment") or "typographic"),
    )
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
        await asyncio.to_thread(
            posts.set_render_meta,
            post_id,
            _render_meta(
                current,
                treatment="generated_image",
                image_prompt=context.get("image_prompt"),
                raw_image_url=new_raw_url,
            ),
        )
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
    await asyncio.to_thread(
        posts.set_render_meta,
        post_id,
        _render_meta(
            revised,
            treatment="generated_image",
            image_prompt=context.get("image_prompt"),
            raw_image_url=raw_url,
        ),
    )
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await twilio_client.send_media(phone, messages.preview_caption(revised), image_url)
    log.info("text edit applied to generated-image post", extra={"post_id": post_id})


async def _edit_vhs_caption(
    phone: str, post_id: str, current: GeneratedPost, feedback: str, context: Row
) -> None:
    """A VHS video post supports caption edits only — the rendered clip is preserved."""
    media_url = str(context.get("media_url") or "")
    await conversation.transition(phone, state=ConversationState.EDITING)
    revised = await editor.apply_edit(
        current, feedback, context={"request": context.get("request")}
    )
    await asyncio.to_thread(
        posts.update,
        post_id,
        caption=revised.caption,
        hashtags=revised.hashtags,
        template_type="vhs",
    )
    await asyncio.to_thread(approvals.record, post_id, "edit_requested", feedback)
    await asyncio.to_thread(
        posts.set_render_meta,
        post_id,
        {**_render_meta(revised, treatment="vhs_video"), "media_url": media_url},
    )
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    if media_url:
        await twilio_client.send_media(
            phone, messages.preview_caption(revised), media_url, post_id=post_id
        )
    else:  # shouldn't happen — fall back to text so Karen still sees the revised copy
        await twilio_client.send_text(phone, messages.preview_caption(revised))
    log.info("vhs caption edit applied", extra={"post_id": post_id})


async def handle_cancellation(phone: str, convo: Row) -> None:
    post_id = convo.get("current_post_id")
    if post_id:
        await asyncio.to_thread(posts.set_status, post_id, "cancelled")
        await asyncio.to_thread(approvals.record, post_id, "cancelled")
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.send_text(phone, "👍 Cancelled. Tell me when you want a new post.")
    log.info("post cancelled", extra={"post_id": post_id})
