"""Inbound-message handler: authorize → classify intent → route → dispatch.

Runs in a FastAPI background task (the webhook has already acked Twilio), so it can
take its time generating + rendering before sending the preview.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai import generator, image_gen, qa, visual_planner
from app.ai import intent as ai_intent
from app.ai import memory as ai_memory
from app.ai.generator import GeneratedPost
from app.ai.intent import Intent, IntentType
from app.config import get_settings
from app.db import approvals, posts, storage
from app.logging_config import get_logger
from app.messaging import conversation, history, media, transcription, twilio_client, video
from app.messaging.conversation import ConversationState
from app.messaging.state_machine import Action, route
from app.messaging.transcription import Outcome
from app.templates import renderer as render_mod
from app.workflows import approval, messages, render_pipeline

# VHS HUD overlay (transparent 9:16), composited onto Karen's video by ffmpeg.
_VHS_OVERLAY_FILE = "_vhs_overlay.html"
_VHS_DIMENSIONS = (1080, 1920)

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


async def handle_incoming_message(
    from_phone: str,
    body: str,
    media: list[Media],
    *,
    message_sid: str | None = None,
    reply_to_sid: str | None = None,
) -> None:
    if not _is_authorized(from_phone):
        log.warning("ignoring message from unauthorized sender", extra={"from": from_phone})
        return

    # Split attachments by kind: a voice note becomes the instruction text; a photo
    # renders on a template; a video gets the VHS treatment. Can co-arrive.
    audio = [m for m in media if m[1].startswith("audio/")]
    photo = next((m for m in media if m[1].startswith("image/")), None)
    clip = next((m for m in media if m[1].startswith("video/")), None)

    if audio:
        transcript = await _transcribe_voice(from_phone, audio[0])
        if transcript is None:
            return  # a failure reply was already sent; nothing to route
        body = transcript  # feed the transcript through the normal text pipeline

    # Log Karen's message to the transcript — powers memory + swipe-reply lookup.
    await history.record_inbound(
        from_phone,
        body=body or None,
        twilio_sid=message_sid,
        media_url=(media[0][0] if media else None),
        kind="voice" if audio else ("image" if photo else ("video" if clip else "text")),
    )

    convo = await conversation.get_or_create(from_phone)
    state = conversation.state_of(convo)

    # If we previously asked "designed graphic or generated image?", this message is
    # the answer — resolve it and build the post, rather than re-classifying intent.
    pending = (convo.get("context") or {}).get("pending_request")
    if state is ConversationState.AWAITING_CLARIFICATION and pending:
        await _resolve_visual_clarification(from_phone, str(pending), body)
        await _update_summary(from_phone)
        return

    memory = await ai_memory.build_context(from_phone, (convo.get("context") or {}).get("summary"))

    intent = await ai_intent.classify_intent(body, state.value, memory)
    # An attached photo/video with no clear text is still a request to build a post
    # from it — don't let a media-only message fall through to "clarify".
    if (photo or clip) and intent.type in (IntentType.greeting, IntentType.unclear):
        intent = Intent(
            type=IntentType.new_post_request,
            extracted_request=body or None,
            confidence=intent.confidence,
        )

    # Swipe-to-reply: if Karen replied to an earlier message about a post, re-open
    # that post so an edit/repost/cancel acts on IT (a question is answered below).
    replied_post_id = await _resolve_reply(reply_to_sid)
    if (
        replied_post_id
        and intent.type
        in (
            IntentType.edit_request,
            IntentType.approval,
            IntentType.cancellation,
        )
        and await _reopen_replied_post(from_phone, replied_post_id)
    ):
        convo = await conversation.get_or_create(from_phone)
        state = conversation.state_of(convo)

    action = route(state, intent.type)
    log.info(
        "routing message",
        extra={"state": str(state), "intent": str(intent.type), "action": str(action)},
    )

    if action is Action.GENERATE:
        await _generate_and_preview(
            from_phone, intent.extracted_request or body, photo, clip, memory=memory
        )
    elif action is Action.APPROVE:
        await approval.handle_approval(from_phone, convo)
    elif action is Action.EDIT:
        await approval.handle_edit_request(from_phone, convo, intent.edit_feedback or body)
    elif action is Action.CANCEL:
        await approval.handle_cancellation(from_phone, convo)
    elif action is Action.ANSWER:
        await _answer_question(from_phone, body, memory, replied_post_id)
    elif action is Action.GREET:
        await twilio_client.send_text(from_phone, messages.GREETING)
    elif action is Action.NUDGE_PENDING:
        await twilio_client.send_text(from_phone, messages.NUDGE_PENDING)
    elif action is Action.NOTHING_PENDING:
        await twilio_client.send_text(from_phone, messages.NOTHING_PENDING)
    elif action is Action.CLARIFY:
        await conversation.transition(from_phone, state=ConversationState.AWAITING_CLARIFICATION)
        await twilio_client.send_text(from_phone, messages.CLARIFY)

    await _update_summary(from_phone)


async def _update_summary(from_phone: str) -> None:
    """Fold any scrolled-out messages into the rolling summary (best-effort)."""
    try:
        convo = await conversation.get_or_create(from_phone)
        await ai_memory.maybe_update_summary(from_phone, convo)
    except Exception as exc:  # noqa: BLE001 — memory upkeep must never break the turn
        log.error("summary update failed", extra={"error": str(exc)})


async def _resolve_reply(reply_to_sid: str | None) -> str | None:
    """Map a swipe-reply's OriginalRepliedMessageSid to the post it concerned, if any."""
    if not reply_to_sid:
        return None
    msg = await history.by_sid(reply_to_sid)
    if msg and msg.get("post_id"):
        return str(msg["post_id"])
    return None


async def _reopen_replied_post(from_phone: str, post_id: str) -> bool:
    """Restore an old post as the active draft so edit/approve/cancel act on it."""
    post = await asyncio.to_thread(posts.get, post_id)
    if not post:
        return False
    meta = post.get("render_meta") or {}
    generated = meta.get("generated") or {
        "caption": post.get("caption") or "",
        "hashtags": post.get("hashtags") or [],
        "template_variant": post.get("template_type") or "promotional",
        "headline": "",
        "rationale": "re-opened from history",
    }
    context_patch: dict[str, Any] = {
        "generated": generated,
        "request": post.get("content"),
        "treatment": meta.get("treatment", "typographic"),
        "pending_request": None,
    }
    if meta.get("image_prompt"):
        context_patch["image_prompt"] = meta["image_prompt"]
    if meta.get("raw_image_url"):
        context_patch["raw_image_url"] = meta["raw_image_url"]
    await conversation.transition(
        from_phone,
        state=ConversationState.AWAITING_APPROVAL,
        current_post_id=post_id,
        context_patch=context_patch,
    )
    log.info("re-opened replied post", extra={"post_id": post_id})
    return True


async def _answer_question(
    from_phone: str, question: str, memory: str, focus_post_id: str | None
) -> None:
    """Answer an informational question from memory + the recent-posts digest."""
    recent_posts = await asyncio.to_thread(posts.recent, 30)
    digest = qa.posts_digest(recent_posts)
    focus = await asyncio.to_thread(posts.get, focus_post_id) if focus_post_id else None
    result = await qa.answer_question(question, memory=memory, digest=digest, focus=focus)
    show_id = result.referenced_post_id or focus_post_id
    show = await asyncio.to_thread(posts.get, show_id) if show_id else None
    if show and show.get("image_url"):
        await twilio_client.send_media(
            from_phone, result.answer, show["image_url"], post_id=show_id
        )
    else:
        await twilio_client.send_text(from_phone, result.answer)


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
    from_phone: str,
    request_text: str,
    photo: Media | None,
    clip: Media | None = None,
    *,
    allow_clarify: bool = True,
    memory: str | None = None,
) -> None:
    """Route a new-post request to the right visual treatment, then preview it.

    A video gets the VHS treatment; a photo renders on a template. Text-only
    requests are planned: a designed typographic template (default), a generated
    image (with the brand overlay), or a clarifying question when ambiguous.
    """
    if clip:
        await _preview_vhs_video(from_phone, request_text, clip, memory=memory)
        return
    if photo:
        await _preview_with_user_photo(from_phone, request_text, photo, memory=memory)
        return

    plan = await visual_planner.plan_visual(request_text, memory)
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
        await _preview_generated(from_phone, request_text, plan.image_prompt, memory=memory)
        return

    # typographic — the default, and the fallback when a re-ask stays unsure
    generated = await generator.generate_freeform(request_text, memory=memory)
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


async def _preview_with_user_photo(
    from_phone: str, request_text: str, photo: Media, *, memory: str | None = None
) -> None:
    """Karen attached a photo: render it on the template (today's behaviour)."""
    photo_bytes: bytes | None = None
    photo_type = "image/jpeg"
    try:
        photo_bytes, photo_type = await media.download_twilio_media(photo[0])
    except Exception as exc:  # noqa: BLE001 — a bad photo shouldn't kill the draft
        log.error("media download failed; continuing without photo", extra={"error": str(exc)})
        photo_bytes = None
    generated = await generator.generate_freeform(
        request_text, image_bytes=photo_bytes, image_media_type=photo_type, memory=memory
    )
    await _finalize_preview(
        from_phone,
        request_text,
        generated,
        image_bytes=photo_bytes,
        image_media_type=photo_type,
        treatment="user_photo",
    )


async def _preview_generated(
    from_phone: str, request_text: str, image_prompt: str, *, memory: str | None = None
) -> None:
    """Generate an image via kie.ai, then overlay the brand template on it."""
    await twilio_client.send_text(from_phone, messages.GENERATING_IMAGE)
    result = await image_gen.generate(image_prompt)
    generated = await generator.generate_freeform(request_text, memory=memory)
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


async def _preview_vhs_video(
    from_phone: str, request_text: str, clip: Media, *, memory: str | None = None
) -> None:
    """Composite the VHS HUD overlay onto Karen's submitted video, then preview it."""
    await twilio_client.send_text(from_phone, messages.PROCESSING_VIDEO)
    try:
        video_bytes, _ = await media.download_twilio_media(clip[0], timeout=60.0)
    except Exception as exc:  # noqa: BLE001 — a fetch failure shouldn't crash the task
        log.error("video download failed", extra={"error": str(exc)})
        await twilio_client.send_text(from_phone, messages.VIDEO_FAILED)
        return

    overlay = await render_mod.renderer.render_file(
        _VHS_OVERLAY_FILE, {}, dimensions=_VHS_DIMENSIONS, transparent=True, scale=1.0
    )
    mp4 = await video.composite_vhs(video_bytes, overlay)
    if not mp4:
        await twilio_client.send_text(from_phone, messages.VIDEO_FAILED)
        return

    generated = await generator.generate_freeform(request_text, memory=memory)
    post = await asyncio.to_thread(
        posts.create,
        content=request_text,
        caption=generated.caption,
        hashtags=generated.hashtags,
        template_type="vhs",
        status="pending_approval",
    )
    post_id = post["id"]
    await asyncio.to_thread(approvals.record, post_id, "generated")
    media_url = await storage.upload_video(post_id, mp4)
    await asyncio.to_thread(posts.set_image_url, post_id, media_url)
    await asyncio.to_thread(
        posts.set_render_meta,
        post_id,
        {"generated": generated.model_dump(), "treatment": "vhs_video", "media_url": media_url},
    )
    await conversation.transition(
        from_phone,
        state=ConversationState.AWAITING_APPROVAL,
        current_post_id=post_id,
        context_patch={
            "generated": generated.model_dump(),
            "request": request_text,
            "treatment": "vhs_video",
            "media_url": media_url,
            "pending_request": None,
        },
    )
    await twilio_client.send_media(
        from_phone, messages.preview_caption(generated), media_url, post_id=post_id
    )
    log.info("vhs video preview sent", extra={"post_id": post_id})


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

    # Persist full render inputs so the post can be re-opened/edited later (swipe-reply).
    render_meta: dict[str, Any] = {"generated": generated.model_dump(), "treatment": treatment}
    if image_prompt is not None:
        render_meta["image_prompt"] = image_prompt
    if raw_image_url is not None:
        render_meta["raw_image_url"] = raw_image_url
    await asyncio.to_thread(posts.set_render_meta, post_id, render_meta)

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
