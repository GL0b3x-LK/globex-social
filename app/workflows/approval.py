"""The approval loop: approve (→ publish), edit (→ re-render new preview), cancel.

Each handler reads the pending draft off the conversation row (current_post_id +
the stored GeneratedPost in context) so it survives restarts.
"""

from __future__ import annotations

import asyncio
from datetime import date as _date
from typing import Any

from app.ai import editor, image_gen, learning
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


async def _merge_render_meta(post_id: str, **changes: Any) -> dict[str, Any]:
    """Update render_meta by MERGING into what is already stored.

    Replacing it wholesale is how an edit silently destroyed a post's identity:
    a scheduled post's ``publish_on`` and ``calendar`` block vanished on the
    first edit, turning a calendar post into an on-demand one and — in
    production — making approval publish it immediately instead of holding for
    its date. Everything not being changed right now is kept.
    """
    post = await asyncio.to_thread(posts.get, post_id)
    meta = dict((post or {}).get("render_meta") or {})
    meta.update({k: v for k, v in changes.items() if v is not None})
    await asyncio.to_thread(posts.set_render_meta, post_id, meta)
    return meta


async def _stored_photo(context: Row, meta: dict[str, Any]) -> tuple[bytes | None, str]:
    """The photograph this post was rendered with, refetched for a re-render.

    Draft time uploads it next to the post (``photo_url``); without it every
    edit re-rendered the template with no photo — the operator watched their
    duck-carton post turn into a flat navy graphic.
    """
    url = str(context.get("photo_url") or meta.get("photo_url") or "")
    media_type = str(
        context.get("photo_media_type") or meta.get("photo_media_type") or "image/jpeg"
    )
    if not url:
        return None, media_type
    try:
        return await image_gen.download(url), media_type
    except Exception as exc:  # noqa: BLE001 — a lost photo must not block the edit
        log.error("could not refetch stored photo", extra={"url": url, "error": str(exc)[:120]})
        return None, media_type


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


async def _maybe_learn(phone: str, feedback: str) -> None:
    """Decide whether this correction should outlive its post.

    Runs AFTER the preview is already on its way — learning must never delay or
    break the edit itself. A standing preference is saved and announced (with an
    undo path); an ambiguous one becomes a question whose answer is the next
    message; a one-off is left alone.
    """
    try:
        decision = await learning.consider(feedback)
        log.info(
            "correction classified",
            extra={"scope": decision.scope, "reason": decision.reason[:120]},
        )
        if decision.scope == "standing" and decision.rule:
            rule = await asyncio.to_thread(
                learning.save_rule, decision.rule, source_feedback=feedback, source=phone
            )
            await twilio_client.send_text(
                phone,
                f"📌 Noted for every future post: {rule.rule}\n"
                "Reply *forget that* if it was just for this one, "
                "or *rules* to see everything I've learned.",
            )
        elif decision.scope == "unsure" and decision.rule:
            await conversation.transition(phone, context_patch={"pending_rule": decision.rule})
            await twilio_client.send_text(
                phone,
                f"Should I do this on every post from now on — “{decision.rule}”?\n"
                "Reply *always* if so; otherwise it's just this once.",
            )
    except Exception as exc:  # noqa: BLE001 — learning is a bonus, never a failure mode
        log.error("learning pass failed", extra={"error": str(exc)[:200]})


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

    # A post that carries a photograph can take PICTURE feedback too: the photo
    # is transformed by the image model (nano-banana img2img), the words stay.
    stored_meta = ((await asyncio.to_thread(posts.get, post_id)) or {}).get("render_meta") or {}
    photo_url = str(context.get("photo_url") or stored_meta.get("photo_url") or "")
    if photo_url and await editor.classify_edit_kind(feedback) == "visual":
        await _edit_post_photo(phone, post_id, current, feedback, photo_url)
        return

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

    # Re-render WITH the photograph the draft was built on — a copy edit changes
    # the words, never the picture.
    photo_bytes, photo_media_type = await _stored_photo(context, stored_meta)
    image_url = await render_pipeline.render_and_store(
        post_id, revised, photo_bytes=photo_bytes, photo_media_type=photo_media_type
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await _merge_render_meta(post_id, generated=revised.model_dump())
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await twilio_client.send_media(
        phone, messages.preview_caption(revised), image_url, post_id=post_id
    )
    log.info("edit applied", extra={"post_id": post_id, "with_photo": photo_bytes is not None})
    await _maybe_learn(phone, feedback)


async def _edit_post_photo(
    phone: str, post_id: str, current: GeneratedPost, feedback: str, photo_url: str
) -> None:
    """Transform a photo post's picture with the image model; the copy is untouched.

    The result becomes the post's stored photograph under a NEW name — the old
    object stays put, because overwriting a public URL fights CDN caching and
    destroys the ability to walk an edit back.
    """
    from uuid import uuid4

    await twilio_client.send_text(phone, messages.REGENERATING_IMAGE)
    result = await image_gen.edit(photo_url, feedback, aspect_ratio="3:4")
    if not result.ok or not result.image_bytes:
        await twilio_client.send_text(phone, messages.IMAGE_EDIT_FAILED)
        await conversation.transition(phone, state=ConversationState.AWAITING_APPROVAL)
        return

    new_url = await asyncio.to_thread(
        storage.upload_bytes,
        f"{post_id}-photo-{uuid4().hex[:6]}.png",
        result.image_bytes,
        "image/png",
    )
    await asyncio.to_thread(approvals.record, post_id, "edit_requested", feedback)
    image_url = await render_pipeline.render_and_store(
        post_id, current, photo_bytes=result.image_bytes, photo_media_type="image/png"
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await _merge_render_meta(post_id, photo_url=new_url, photo_media_type="image/png")
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"photo_url": new_url, "photo_media_type": "image/png"},
    )
    await twilio_client.send_media(
        phone, messages.preview_caption(current), image_url, post_id=post_id
    )
    log.info("photo edit applied", extra={"post_id": post_id})
    await _maybe_learn(phone, feedback)


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
        await _merge_render_meta(post_id, generated=current.model_dump(), raw_image_url=new_raw_url)
        await conversation.transition(
            phone,
            state=ConversationState.AWAITING_APPROVAL,
            context_patch={"raw_image_url": new_raw_url},
        )
        await twilio_client.send_media(
            phone, messages.preview_caption(current), image_url, post_id=post_id
        )
        log.info("image edit applied", extra={"post_id": post_id})
        await _maybe_learn(phone, feedback)
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
    await _merge_render_meta(post_id, generated=revised.model_dump())
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await twilio_client.send_media(
        phone, messages.preview_caption(revised), image_url, post_id=post_id
    )
    log.info("text edit applied to generated-image post", extra={"post_id": post_id})
    await _maybe_learn(phone, feedback)


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
    await _merge_render_meta(post_id, generated=revised.model_dump(), media_url=media_url or None)
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
    await _maybe_learn(phone, feedback)


async def handle_cancellation(phone: str, convo: Row) -> None:
    post_id = convo.get("current_post_id")
    if post_id:
        await asyncio.to_thread(posts.set_status, post_id, "cancelled")
        await asyncio.to_thread(approvals.record, post_id, "cancelled")
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.send_text(phone, "👍 Cancelled. Tell me when you want a new post.")
    log.info("post cancelled", extra={"post_id": post_id})
