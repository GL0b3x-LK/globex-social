"""The approval loop: approve (→ publish), edit (→ re-render new preview), cancel.

Each handler reads the pending draft off the conversation row (current_post_id +
the stored GeneratedPost in context) so it survives restarts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import clock
from app.ai import editor, image_gen, learning
from app.ai.generator import GeneratedPost
from app.db import approvals, posts, storage
from app.logging_config import get_logger
from app.messaging import conversation, media, twilio_client
from app.messaging.conversation import ConversationState
from app.publishing import platforms as plat
from app.publishing import publisher
from app.workflows import asset_bank, messages, redelivery, render_pipeline

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


async def _deliver_preview(
    phone: str, post_id: str, post: GeneratedPost, image_url: str, caption: str | None = None
) -> bool:
    """Send a preview and remember whether it actually got there.

    A send can fail for reasons that have nothing to do with the post — a closed
    24h window, a lapsed sandbox join, the daily message cap. The work is already
    done and stored by this point, so a failure must not be the end of it: the
    post is marked undelivered and ``redelivery`` re-sends it once sending works
    again, instead of the operator silently never seeing what they asked for.
    """
    sid = await twilio_client.try_send_media(
        phone,
        caption if caption is not None else messages.preview_caption(post),
        image_url,
        post_id=post_id,
    )
    await redelivery.record(post_id, phone, delivered=sid is not None)
    return sid is not None


async def _picture_from_bank(
    phone: str, feedback: str, *, aspect_ratio: str = "1:1"
) -> tuple[bytes, str] | None:
    """The picture the operator named, when they named one we already own.

    Until this existed the bank was reachable only at draft time, so "use the
    hero lamb shot" could only be honoured by asking an image model to redraw
    the current picture into something lamb-like — an invention, when the actual
    photograph was sitting in storage.

    Two shapes, both truer than redrawing:

    * a shot on its own is fetched and used as-is — no model, no cost, no drift
      away from the photograph they asked for by name;
    * a person plus a shot goes to the multi-reference model together, which is
      the only way "Priya holding the lamb" comes back as our Priya holding our
      lamb rather than a stranger holding a stock cut.

    Returns None when nothing is named, when the operator explicitly asked for
    something new, or when the fetch fails — every one of which means "fall
    through and redraw", never "give up on the edit".
    """
    if asset_bank.wants_new_image(feedback):
        return None
    refs = asset_bank.resolve_refs(feedback)
    if not refs:
        return None

    if refs.character is None and refs.asset is not None:
        try:
            data = await image_gen.download(refs.asset.url)
        except Exception as exc:  # noqa: BLE001 — a bad fetch falls back, never fails the edit
            log.error(
                "bank asset fetch failed",
                extra={"asset": refs.asset.file, "error": str(exc)[:200]},
            )
            return None
        await twilio_client.try_send_text(phone, messages.swapped_from_bank(refs.asset.label))
        log.info("picture swapped from the bank", extra={"asset": refs.asset.file})
        return data, "image/jpeg"

    await twilio_client.try_send_text(phone, messages.composing_from_bank(refs.names))
    result = await image_gen.edit_multi(
        refs.urls, asset_bank.compose_prompt(feedback, refs), aspect_ratio=aspect_ratio
    )
    if not result.ok or not result.image_bytes:
        log.error("bank composition failed", extra={"refs": refs.names, "error": result.error})
        return None
    log.info("picture composed from the bank", extra={"refs": refs.names})
    return result.image_bytes, "image/png"


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
        await twilio_client.try_send_text(phone, messages.NOTHING_PENDING)
        return
    # Last-chance platform override at approval ("approve — just LinkedIn").
    if target_platforms:
        await asyncio.to_thread(
            posts.set_target_platforms, post_id, [p.value for p in target_platforms]
        )
    await asyncio.to_thread(posts.set_status, post_id, "approved")
    await asyncio.to_thread(approvals.record, post_id, "approved")

    # Calendar-scheduled posts hold until their moment; the scheduler publishes
    # them. "Their moment" is 1am New York on the post date, not merely "a later
    # date": the server clock is UTC and rolls over at 8pm New York, so a date
    # comparison released the hold — and published — four hours early for any
    # approval given that evening.
    post = await asyncio.to_thread(posts.get, post_id)
    meta = (post or {}).get("render_meta") or {}
    publish_on = meta.get("publish_on")
    if publish_on and not clock.is_due(publish_on):
        await conversation.transition(phone, state=ConversationState.IDLE)
        await conversation.clear_post(phone)
        moment = clock.publish_moment(publish_on)
        pretty = moment.strftime("%A %d %B at %-I%p").replace("AM", "am").replace("PM", "pm")
        await twilio_client.try_send_text(
            phone, f"✅ Approved — it will go out automatically on {pretty}."
        )
        log.info("post approved (scheduled)", extra={"post_id": post_id, "publish_on": publish_on})
        return

    results = await publisher.publish_post(post_id)  # real multi-platform publish via Blotato
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.try_send_text(phone, messages.publish_status(results))
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
            await twilio_client.try_send_text(
                phone,
                f"📌 Noted for every future post: {rule.rule}\n"
                "Reply *forget that* if it was just for this one, "
                "or *rules* to see everything I've learned.",
            )
        elif decision.scope == "unsure" and decision.rule:
            await conversation.transition(phone, context_patch={"pending_rule": decision.rule})
            await twilio_client.try_send_text(
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
    photo: tuple[str, str] | None = None,
) -> None:
    """Apply an edit. ``photo`` is an image attached to the operator's message —
    a replacement picture for this post, not a prompt for the image model."""
    post_id = convo.get("current_post_id")
    context = convo.get("context") or {}
    stored = context.get("generated")
    if not post_id or not stored:
        await twilio_client.try_send_text(phone, messages.NOTHING_PENDING)
        return

    # Platform change mid-draft ("actually just LinkedIn") — update the target now.
    if target_platforms:
        await asyncio.to_thread(
            posts.set_target_platforms, post_id, [p.value for p in target_platforms]
        )

    current = GeneratedPost(**stored)

    # An attached image is a REPLACEMENT photograph for this post. It used to be
    # discarded outright: the milestone previews invite "reply with the employee's
    # photo to swap it in", the reply arrived, and the picture never changed.
    # Downloaded BEFORE the treatment branch — generated-image posts take a
    # replacement too, and taking it after the branch meant one silently landed
    # on the floor for exactly the posts most likely to want a real photo.
    replacement: tuple[bytes, str] | None = None
    if photo is not None:
        try:
            replacement = await media.download_twilio_media(photo[0])
        except Exception as exc:  # noqa: BLE001 — a bad download must not lose the edit
            log.error("attached photo download failed", extra={"error": str(exc)[:200]})
            await twilio_client.try_send_text(phone, messages.PHOTO_DOWNLOAD_FAILED)

    if context.get("treatment") == "generated_image":
        await _edit_generated_image(
            phone, post_id, current, feedback, context, replacement=replacement
        )
        return
    if context.get("treatment") == "vhs_video":
        await _edit_vhs_caption(phone, post_id, current, feedback, context)
        return

    await conversation.transition(phone, state=ConversationState.EDITING)

    # A post that carries a photograph can take PICTURE feedback too: the photo
    # is transformed by the image model (nano-banana img2img), the words stay.
    stored_meta = ((await asyncio.to_thread(posts.get, post_id)) or {}).get("render_meta") or {}
    photo_url = str(context.get("photo_url") or stored_meta.get("photo_url") or "")
    if photo_url or replacement is not None:
        kind = await editor.classify_edit_kind(feedback)
        await _edit_photo_post(
            phone,
            post_id,
            current,
            feedback,
            photo_url,
            kind=kind,
            replacement=replacement,
            is_placeholder=bool(stored_meta.get("photo_is_placeholder")),
            context=context,
        )
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
        post_id, revised, photo_bytes=photo_bytes, photo_media_type=photo_media_type, fresh=True
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await _merge_render_meta(post_id, generated=revised.model_dump())
    await conversation.transition(
        phone,
        state=ConversationState.AWAITING_APPROVAL,
        context_patch={"generated": revised.model_dump()},
    )
    await _deliver_preview(phone, post_id, revised, image_url)
    log.info("edit applied", extra={"post_id": post_id, "with_photo": photo_bytes is not None})
    await _maybe_learn(phone, feedback)


async def _edit_photo_post(
    phone: str,
    post_id: str,
    current: GeneratedPost,
    feedback: str,
    photo_url: str,
    *,
    kind: editor.EditKind,
    replacement: tuple[bytes, str] | None = None,
    is_placeholder: bool = False,
    context: Row | None = None,
) -> None:
    """Apply an edit to a post that carries a photograph.

    All three halves of an edit are honoured independently, because a single
    WhatsApp line routinely asks for more than one at once:

    * an ATTACHED photo replaces the picture outright (no image model involved —
      the operator sent the exact image they want);
    * a VISUAL instruction transforms the stored picture through img2img;
    * a TEXTUAL instruction rewrites the copy.

    A "both" edit runs the picture and the copy changes together; previously the
    classifier had to pick one and the loser was dropped in silence.

    Pictures are stored under a NEW name every time — overwriting a public URL
    fights CDN caching and destroys the ability to walk an edit back.
    """
    from uuid import uuid4

    ctx = context or {}
    wants_picture = kind in ("visual", "both")
    wants_words = kind in ("textual", "both")

    # A placeholder is not a photograph of anyone — it is a gray card standing in
    # until a real one arrives. Running img2img on it invents a person and puts a
    # fabricated face on a named employee's post, one "approve" from publishing.
    if wants_picture and replacement is None and is_placeholder:
        await twilio_client.try_send_text(phone, messages.PLACEHOLDER_NEEDS_PHOTO)
        if not wants_words:
            await conversation.transition(phone, state=ConversationState.AWAITING_APPROVAL)
            return
        wants_picture = False

    photo_bytes: bytes | None = None
    media_type = "image/jpeg"
    new_photo_url: str | None = None
    still_placeholder = is_placeholder

    if replacement is not None:
        photo_bytes, media_type = replacement
        still_placeholder = False
    elif wants_picture and (bank := await _picture_from_bank(phone, feedback, aspect_ratio="3:4")):
        photo_bytes, media_type = bank
        still_placeholder = False
    elif wants_picture:
        await twilio_client.try_send_text(phone, messages.REGENERATING_IMAGE)
        result = await image_gen.edit(photo_url, feedback, aspect_ratio="3:4")
        if not result.ok or not result.image_bytes:
            await twilio_client.try_send_text(phone, messages.IMAGE_EDIT_FAILED)
            if not wants_words:
                await conversation.transition(phone, state=ConversationState.AWAITING_APPROVAL)
                return
        else:
            photo_bytes, media_type = result.image_bytes, "image/png"

    if photo_bytes is not None:
        ext = "png" if media_type == "image/png" else "jpg"
        new_photo_url = await asyncio.to_thread(
            storage.upload_bytes,
            f"{post_id}-photo-{uuid4().hex[:6]}.{ext}",
            photo_bytes,
            media_type,
        )
    else:  # copy-only edit — re-render on the picture the post already has
        photo_bytes, media_type = await _stored_photo(ctx, {"photo_url": photo_url})

    revised = current
    if wants_words:
        revised = await editor.apply_edit(
            current, feedback, context={"request": ctx.get("request")}
        )
        await asyncio.to_thread(
            posts.update,
            post_id,
            caption=revised.caption,
            hashtags=revised.hashtags,
            template_type=revised.template_variant,
        )

    await asyncio.to_thread(approvals.record, post_id, "edit_requested", feedback)
    image_url = await render_pipeline.render_and_store(
        post_id, revised, photo_bytes=photo_bytes, photo_media_type=media_type, fresh=True
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await _merge_render_meta(
        post_id,
        generated=revised.model_dump() if wants_words else None,
        photo_url=new_photo_url,
        photo_media_type=media_type if new_photo_url else None,
        photo_is_placeholder=still_placeholder,
    )
    patch: dict[str, Any] = {}
    if wants_words:
        patch["generated"] = revised.model_dump()
    if new_photo_url:
        patch["photo_url"] = new_photo_url
        patch["photo_media_type"] = media_type
    await conversation.transition(
        phone, state=ConversationState.AWAITING_APPROVAL, context_patch=patch or None
    )
    await _deliver_preview(phone, post_id, revised, image_url)
    log.info(
        "photo post edit applied",
        extra={
            "post_id": post_id,
            "kind": kind,
            "replaced_photo": replacement is not None,
            "words_changed": wants_words,
        },
    )
    await _maybe_learn(phone, feedback)


async def _edit_generated_image(
    phone: str,
    post_id: str,
    current: GeneratedPost,
    feedback: str,
    context: Row,
    *,
    replacement: tuple[bytes, str] | None = None,
) -> None:
    """Apply an edit to a post built on an AI-generated picture.

    The three halves of an edit are honoured independently, exactly as for photo
    posts (`_edit_photo_post`), because one WhatsApp line routinely asks for more
    than one at once:

    * an ATTACHED photo replaces the picture outright (no image model involved);
    * a VISUAL instruction transforms the stored picture through img2img;
    * a TEXTUAL instruction rewrites the copy.

    This branch used to test ``kind == "visual"`` alone, so a "both" edit —
    "make it brighter and change the headline to X" — rewrote the words and left
    the picture untouched without saying so. That is the same fault already fixed
    on the photo path; this is the branch it was missed on.

    img2img always references the RAW generated image, never the delivered
    preview: hand the model back the flattened poster and it redraws the logo and
    headline into the photograph.
    """
    from uuid import uuid4

    raw_url = context.get("raw_image_url")
    await conversation.transition(phone, state=ConversationState.EDITING)
    kind = await editor.classify_edit_kind(feedback)
    wants_picture = kind in ("visual", "both")
    wants_words = kind in ("textual", "both")

    new_raw_url: str | None = None
    photo_bytes: bytes | None = None
    media_type = "image/png"  # anything the image model returns; an upload may differ

    if replacement is not None:
        photo_bytes, media_type = replacement
    elif wants_picture and (bank := await _picture_from_bank(phone, feedback)):
        photo_bytes, media_type = bank
    elif wants_picture and raw_url:
        await twilio_client.try_send_text(phone, messages.REGENERATING_IMAGE)
        result = await image_gen.edit(str(raw_url), feedback)
        if not result.ok or not result.image_bytes:
            await twilio_client.try_send_text(phone, messages.IMAGE_EDIT_FAILED)
            if not wants_words:
                await conversation.transition(phone, state=ConversationState.AWAITING_APPROVAL)
                return
        else:
            photo_bytes = result.image_bytes

    if photo_bytes is not None:
        # Stored as the new RAW layer so the next img2img edit chains off this
        # picture rather than the one it replaced. A NEW object every time:
        # overwriting `{post_id}-raw.png` in place left the URL handed to the
        # image model behind Supabase's hour-long CDN cache, so a second tweak
        # could silently transform the PREVIOUS picture — the chaining breaks
        # exactly where it matters most.
        ext = "png" if media_type == "image/png" else "jpg"
        new_raw_url = await asyncio.to_thread(
            storage.upload_bytes,
            f"{post_id}-raw-{uuid4().hex[:6]}.{ext}",
            photo_bytes,
            media_type,
        )
    elif raw_url:  # copy-only edit — re-render the overlay on the SAME picture
        try:
            photo_bytes = await image_gen.download(str(raw_url))
        except Exception as exc:  # noqa: BLE001 — keep the edit working even if refetch fails
            log.error("could not refetch raw image for re-overlay", extra={"error": str(exc)})

    revised = current
    if wants_words:
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
    image_url = await render_pipeline.render_and_store(
        post_id, revised, photo_bytes=photo_bytes, photo_media_type=media_type, fresh=True
    )
    await asyncio.to_thread(posts.set_image_url, post_id, image_url)
    await _merge_render_meta(
        post_id,
        generated=revised.model_dump() if wants_words else None,
        raw_image_url=new_raw_url,
    )
    patch: dict[str, Any] = {}
    if wants_words:
        patch["generated"] = revised.model_dump()
    if new_raw_url:
        patch["raw_image_url"] = new_raw_url
    await conversation.transition(
        phone, state=ConversationState.AWAITING_APPROVAL, context_patch=patch or None
    )
    await _deliver_preview(phone, post_id, revised, image_url)
    log.info(
        "generated-image edit applied",
        extra={
            "post_id": post_id,
            "kind": kind,
            "replaced_photo": replacement is not None,
            "words_changed": wants_words,
        },
    )
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
        await twilio_client.try_send_media(
            phone, messages.preview_caption(revised), media_url, post_id=post_id
        )
    else:  # shouldn't happen — fall back to text so Karen still sees the revised copy
        await twilio_client.try_send_text(phone, messages.preview_caption(revised))
    log.info("vhs caption edit applied", extra={"post_id": post_id})
    await _maybe_learn(phone, feedback)


async def handle_cancellation(phone: str, convo: Row) -> None:
    post_id = convo.get("current_post_id")
    if post_id:
        await asyncio.to_thread(posts.set_status, post_id, "cancelled")
        await asyncio.to_thread(approvals.record, post_id, "cancelled")
    await conversation.transition(phone, state=ConversationState.IDLE)
    await conversation.clear_post(phone)
    await twilio_client.try_send_text(phone, "👍 Cancelled. Tell me when you want a new post.")
    log.info("post cancelled", extra={"post_id": post_id})
