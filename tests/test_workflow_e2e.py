"""End-to-end workflow tests (always-on, fully offline).

Drives on_demand.handle_incoming_message through the real routing + handlers, with
the externals (intent classify, generate, edit, render+upload, Twilio, publish) and
the DB (conversations/posts/approvals) replaced by in-memory fakes. This proves the
wiring — routing, state transitions, the approve/edit/cancel loop — without any
network, AI spend, browser, or Supabase.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai import image_gen
from app.ai.generator import GeneratedPost
from app.ai.intent import Intent, IntentType
from app.ai.visual_planner import VisualPlan
from app.messaging.transcription import Outcome, Transcript
from app.workflows import on_demand

_TYPO_PLAN = VisualPlan(treatment="typographic", rationale="a normal text post")

PHONE = "whatsapp:+19170001111"

_DRAFT = GeneratedPost(
    caption="On the floor at SIAL Paris — talking protein supply at global scale.",
    hashtags=["#GlobexInternational", "#SIAL"],
    template_variant="trade_show_pre",
    headline="Meet us at SIAL",
    subhead="Let's talk sourcing.",
    rationale="Trade-show pre-event fits the request.",
)
_REVISED = _DRAFT.model_copy(update={"headline": "See us at SIAL", "caption": "Shorter."})


async def _fake_classify(message: str, state, memory=None) -> Intent:
    m = message.lower()
    if "?" in m or any(m.startswith(w) for w in ("how ", "what ", "when ", "show ", "which ")):
        return Intent(type=IntentType.question, confidence=0.9)
    if any(w in m for w in ("approve", "looks good", "ship it", "yes")):
        return Intent(type=IntentType.approval, confidence=0.95)
    if any(w in m for w in ("cancel", "nevermind", "forget")):
        return Intent(type=IntentType.cancellation, confidence=0.95)
    if m.startswith("make ") or "shorter" in m or "change" in m:
        return Intent(type=IntentType.edit_request, edit_feedback=message, confidence=0.95)
    if "post" in m or "about" in m:
        return Intent(type=IntentType.new_post_request, extracted_request=message, confidence=0.95)
    return Intent(type=IntentType.unclear, confidence=0.4)


@pytest.fixture
def harness(monkeypatch):
    convos: dict[str, dict] = {}
    posts_store: dict[str, dict] = {}
    sent_text: list[tuple[str, str]] = []
    sent_media: list[tuple[str, str, str]] = []
    published: list[str] = []
    counter = {"n": 0}

    def _blank(phone: str) -> dict:
        return {"phone_number": phone, "state": "idle", "context": {}, "current_post_id": None}

    async def get_or_create(phone):
        return convos.setdefault(phone, _blank(phone))

    async def transition(phone, *, state=None, current_post_id=None, context_patch=None):
        c = convos.setdefault(phone, _blank(phone))
        if state is not None:
            c["state"] = str(state)
        if current_post_id is not None:
            c["current_post_id"] = current_post_id
        if context_patch:
            c["context"] = {**(c.get("context") or {}), **context_patch}
        return c

    async def clear_post(phone):
        convos[phone]["current_post_id"] = None
        return convos[phone]

    def create(**kw):
        counter["n"] += 1
        pid = f"post-{counter['n']}"
        posts_store[pid] = {"id": pid, **kw}
        return posts_store[pid]

    monkeypatch.setattr("app.messaging.conversation.get_or_create", get_or_create)
    monkeypatch.setattr("app.messaging.conversation.transition", transition)
    monkeypatch.setattr("app.messaging.conversation.clear_post", clear_post)
    monkeypatch.setattr("app.db.posts.create", create)
    monkeypatch.setattr("app.db.posts.update", lambda pid, **f: posts_store[pid].update(f))
    monkeypatch.setattr(
        "app.db.posts.set_status", lambda pid, s: posts_store[pid].update({"status": s})
    )
    monkeypatch.setattr(
        "app.db.posts.set_image_url", lambda pid, u: posts_store[pid].update({"image_url": u})
    )
    monkeypatch.setattr("app.db.approvals.record", lambda *a, **k: {"id": "ah"})
    monkeypatch.setattr("app.ai.intent.classify_intent", _fake_classify)
    monkeypatch.setattr("app.ai.generator.generate_freeform", lambda *a, **k: _async(_DRAFT))
    monkeypatch.setattr("app.ai.editor.apply_edit", lambda *a, **k: _async(_REVISED))
    monkeypatch.setattr(
        "app.messaging.media.download_twilio_media",
        lambda url, **k: _async((b"fake-image-bytes", "image/jpeg")),
    )
    monkeypatch.setattr(
        "app.workflows.render_pipeline.render_and_store",
        lambda post_id, post, **k: _async(f"https://img.test/{post_id}.png"),
    )
    monkeypatch.setattr(
        "app.messaging.twilio_client.send_text",
        lambda to, body, **k: _async(sent_text.append((to, body))),
    )
    monkeypatch.setattr(
        "app.messaging.twilio_client.send_media",
        lambda to, body, url, **k: _async(sent_media.append((to, body, url))),
    )
    monkeypatch.setattr(
        "app.publishing.publisher.publish_post", lambda pid: _async(published.append(pid))
    )
    monkeypatch.setattr(
        "app.workflows.on_demand.get_settings",
        lambda: SimpleNamespace(authorized_numbers_list=[PHONE]),
    )
    # Image-generation seams — default to a plain typographic plan so the existing
    # text/voice tests are unaffected; image tests override these.
    monkeypatch.setattr("app.ai.visual_planner.plan_visual", lambda *a, **k: _async(_TYPO_PLAN))
    monkeypatch.setattr(
        "app.db.storage.upload_png",
        lambda pid, b, **k: _async(f"https://img.test/{pid}{k.get('suffix', '')}.png"),
    )
    monkeypatch.setattr(
        "app.ai.image_gen.generate",
        lambda *a, **k: _async(image_gen.ImageResult(ok=True, image_bytes=b"genpng")),
    )
    monkeypatch.setattr(
        "app.ai.image_gen.edit",
        lambda *a, **k: _async(image_gen.ImageResult(ok=True, image_bytes=b"editpng")),
    )
    monkeypatch.setattr("app.ai.image_gen.download", lambda url: _async(b"rawpng"))
    monkeypatch.setattr("app.ai.editor.classify_edit_kind", lambda fb: _async("textual"))
    # Memory + transcript seams — default to empty memory so existing tests are
    # unaffected; memory/reply/Q&A tests override these.
    monkeypatch.setattr("app.messaging.history.record_inbound", lambda *a, **k: _async(None))
    monkeypatch.setattr("app.ai.memory.build_context", lambda *a, **k: _async(""))
    monkeypatch.setattr("app.ai.memory.maybe_update_summary", lambda *a, **k: _async(None))
    monkeypatch.setattr("app.messaging.history.by_sid", lambda sid: _async(None))
    monkeypatch.setattr("app.db.posts.get", lambda pid: _async_noop_get(posts_store, pid))
    monkeypatch.setattr("app.db.posts.recent", lambda limit=30: list(posts_store.values()))
    monkeypatch.setattr(
        "app.db.posts.set_render_meta",
        lambda pid, meta: posts_store[pid].update({"render_meta": meta}),
    )
    # VHS video seams — overlay render, ffmpeg composite, and video upload.
    monkeypatch.setattr(
        "app.templates.renderer.renderer.render_file", lambda *a, **k: _async(b"overlaypng")
    )
    monkeypatch.setattr("app.messaging.video.composite_vhs", lambda *a, **k: _async(b"mp4bytes"))
    monkeypatch.setattr(
        "app.db.storage.upload_video", lambda pid, b: _async(f"https://vid.test/{pid}.mp4")
    )
    return SimpleNamespace(
        convos=convos,
        posts=posts_store,
        sent_text=sent_text,
        sent_media=sent_media,
        published=published,
    )


def _async_noop_get(store, pid):
    # posts.get is sync (called via asyncio.to_thread), so return the row directly.
    return store.get(pid)


async def _async(value):
    return value


async def test_new_post_request_sends_preview_and_awaits_approval(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "post about us at SIAL Paris", [])
    assert len(harness.sent_media) == 1  # preview image sent
    convo = harness.convos[PHONE]
    assert convo["state"] == "awaiting_approval"
    assert convo["current_post_id"] in harness.posts
    assert harness.posts[convo["current_post_id"]]["status"] == "pending_approval"


async def test_approval_triggers_publish_and_resets(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "post about us at SIAL Paris", [])
    post_id = harness.convos[PHONE]["current_post_id"]
    await on_demand.handle_incoming_message(PHONE, "approve", [])
    assert harness.published == [post_id]
    assert harness.posts[post_id]["status"] == "approved"
    assert harness.convos[PHONE]["state"] == "idle"
    assert harness.convos[PHONE]["current_post_id"] is None


async def test_edit_request_re_renders_and_stays_pending(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "post about us at SIAL Paris", [])
    await on_demand.handle_incoming_message(PHONE, "make it shorter", [])
    assert len(harness.sent_media) == 2  # original preview + edited preview
    assert harness.convos[PHONE]["state"] == "awaiting_approval"
    assert harness.convos[PHONE]["context"]["generated"]["caption"] == "Shorter."
    assert not harness.published


async def test_cancellation_resets_without_publish(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "post about us at SIAL Paris", [])
    post_id = harness.convos[PHONE]["current_post_id"]
    await on_demand.handle_incoming_message(PHONE, "cancel", [])
    assert harness.posts[post_id]["status"] == "cancelled"
    assert harness.convos[PHONE]["state"] == "idle"
    assert harness.convos[PHONE]["current_post_id"] is None
    assert not harness.published


async def test_approval_with_nothing_pending_is_a_no_op(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "approve", [])  # IDLE + approval
    assert not harness.published
    assert harness.sent_text and "Nothing" in harness.sent_text[-1][1]


async def test_unauthorized_sender_is_ignored(harness) -> None:
    await on_demand.handle_incoming_message("whatsapp:+10000000000", "post about X", [])
    assert not harness.sent_media and not harness.sent_text


async def test_image_only_message_still_generates(harness) -> None:
    # An attached photo with no caption must build a post, not fall through to "clarify".
    await on_demand.handle_incoming_message(
        PHONE, "", [("https://media.twiliocdn.test/x.jpg", "image/jpeg")]
    )
    assert len(harness.sent_media) == 1
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


# --- Voice notes: a transcript flows through the same pipeline as typed text. ---

_VOICE = [("https://media.twiliocdn.test/v.ogg", "audio/ogg")]


def _transcript(outcome, text=""):
    async def _fake(audio_bytes, content_type):
        return Transcript(outcome, text)

    return _fake


async def test_voice_note_transcribes_echoes_and_drafts(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.messaging.transcription.transcribe",
        _transcript(Outcome.ok, "post about us at SIAL Paris"),
    )
    await on_demand.handle_incoming_message(PHONE, "", _VOICE)
    assert any("Heard" in body for _, body in harness.sent_text)  # echoed what it heard
    assert len(harness.sent_media) == 1  # then drafted + previewed
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


async def test_voice_note_can_approve(harness, monkeypatch) -> None:
    await on_demand.handle_incoming_message(PHONE, "post about us at SIAL Paris", [])
    post_id = harness.convos[PHONE]["current_post_id"]
    monkeypatch.setattr(
        "app.messaging.transcription.transcribe", _transcript(Outcome.ok, "approve")
    )
    await on_demand.handle_incoming_message(PHONE, "", _VOICE)
    assert harness.published == [post_id]  # voice 'approve' publishes (echo-all, never-block)


async def test_voice_with_photo_uses_transcript_as_instruction(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.messaging.transcription.transcribe",
        _transcript(Outcome.ok, "post about this at SIAL Paris"),
    )
    await on_demand.handle_incoming_message(
        PHONE, "", [*_VOICE, ("https://media.twiliocdn.test/p.jpg", "image/jpeg")]
    )
    assert len(harness.sent_media) == 1
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


async def test_voice_note_no_speech_is_friendly_and_routes_nothing(harness, monkeypatch) -> None:
    monkeypatch.setattr("app.messaging.transcription.transcribe", _transcript(Outcome.no_speech))
    await on_demand.handle_incoming_message(PHONE, "", _VOICE)
    assert harness.sent_text and "couldn't make out" in harness.sent_text[-1][1]
    assert not harness.sent_media  # nothing generated
    assert PHONE not in harness.convos  # bailed before touching conversation state


# --- AI image generation: a generated image is overlaid on the brand template. ---


def _plan(treatment, *, image_prompt=None, clarification=None):
    return VisualPlan(
        treatment=treatment,
        image_prompt=image_prompt,
        clarification=clarification,
        rationale="test",
    )


async def test_generated_image_request_overlays_and_previews(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ai.visual_planner.plan_visual",
        lambda *a, **k: _async(_plan("generated_image", image_prompt="a port at dawn")),
    )
    await on_demand.handle_incoming_message(PHONE, "generate an image post about our port", [])
    assert any("Generating" in body for _, body in harness.sent_text)  # interim heads-up
    assert len(harness.sent_media) == 1  # branded preview
    convo = harness.convos[PHONE]
    assert convo["state"] == "awaiting_approval"
    assert convo["context"]["treatment"] == "generated_image"
    assert convo["context"]["raw_image_url"]  # raw image persisted for img2img edits
    assert convo["context"]["image_prompt"] == "a port at dawn"


async def test_image_generation_failure_falls_back_to_typographic(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ai.visual_planner.plan_visual",
        lambda *a, **k: _async(_plan("generated_image", image_prompt="a port")),
    )
    monkeypatch.setattr(
        "app.ai.image_gen.generate",
        lambda *a, **k: _async(image_gen.ImageResult(ok=False, error="boom")),
    )
    await on_demand.handle_incoming_message(PHONE, "generate an image post about our port", [])
    assert any("couldn't generate" in body for _, body in harness.sent_text)
    assert len(harness.sent_media) == 1  # still got a designed version
    assert harness.convos[PHONE]["context"]["treatment"] == "typographic"


async def test_ambiguous_request_asks_then_resolves_to_image(harness, monkeypatch) -> None:
    plans = [
        _plan("clarify", clarification="Designed graphic or a generated photo?"),
        _plan("generated_image", image_prompt="a duck farm at golden hour"),
    ]

    async def _stateful_plan(*a, **k):
        return plans.pop(0)

    monkeypatch.setattr("app.ai.visual_planner.plan_visual", _stateful_plan)
    # 1) ambiguous request → the agent asks
    await on_demand.handle_incoming_message(PHONE, "post about our duck line", [])
    assert harness.convos[PHONE]["state"] == "awaiting_clarification"
    assert any("generated photo" in body for _, body in harness.sent_text)
    assert not harness.sent_media
    # 2) Karen answers → it resolves and builds the image post
    await on_demand.handle_incoming_message(PHONE, "yeah generate a photo for it", [])
    assert len(harness.sent_media) == 1
    assert harness.convos[PHONE]["state"] == "awaiting_approval"
    assert harness.convos[PHONE]["context"]["treatment"] == "generated_image"


async def _make_generated_draft(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ai.visual_planner.plan_visual",
        lambda *a, **k: _async(_plan("generated_image", image_prompt="a port")),
    )
    await on_demand.handle_incoming_message(PHONE, "generate an image post about our port", [])


async def test_visual_edit_regenerates_the_image(harness, monkeypatch) -> None:
    await _make_generated_draft(harness, monkeypatch)
    before = len(harness.sent_media)
    edited: list = []

    async def _fake_edit(url, fb, **k):
        edited.append((url, fb))
        return image_gen.ImageResult(ok=True, image_bytes=b"editpng")

    monkeypatch.setattr("app.ai.editor.classify_edit_kind", lambda fb: _async("visual"))
    monkeypatch.setattr("app.ai.image_gen.edit", _fake_edit)
    await on_demand.handle_incoming_message(PHONE, "make it a sunset", [])
    assert edited  # img2img was invoked on the stored raw image
    assert any("Updating the image" in body for _, body in harness.sent_text)
    assert len(harness.sent_media) == before + 1
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


async def test_textual_edit_keeps_the_same_image(harness, monkeypatch) -> None:
    await _make_generated_draft(harness, monkeypatch)
    edit_called: list = []

    async def _fake_edit(*a, **k):
        edit_called.append(1)
        return image_gen.ImageResult(ok=False)

    monkeypatch.setattr("app.ai.editor.classify_edit_kind", lambda fb: _async("textual"))
    monkeypatch.setattr("app.ai.image_gen.edit", _fake_edit)
    await on_demand.handle_incoming_message(PHONE, "make it shorter", [])
    assert not edit_called  # no regeneration — same picture, new copy
    assert harness.convos[PHONE]["context"]["generated"]["caption"] == "Shorter."
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


# --- Persistent memory + swipe-to-reply + conversational Q&A. ---


def _seed_post(harness, pid, **fields) -> None:
    base = {
        "id": pid,
        "caption": "Old caption",
        "hashtags": ["#GlobexInternational"],
        "template_type": "stats",
        "content": "original request",
        "status": "published",
        "image_url": f"https://img.test/{pid}.png",
        "render_meta": {
            "generated": {
                "caption": "Old caption",
                "hashtags": ["#GlobexInternational"],
                "template_variant": "stats",
                "headline": "Old headline",
                "rationale": "r",
            },
            "treatment": "typographic",
        },
    }
    base.update(fields)
    harness.posts[pid] = base


async def test_inbound_message_is_logged_with_sid(harness, monkeypatch) -> None:
    logged: list = []
    monkeypatch.setattr(
        "app.messaging.history.record_inbound",
        lambda phone, **k: _async(logged.append((phone, k.get("body"), k.get("twilio_sid")))),
    )
    await on_demand.handle_incoming_message(PHONE, "post about SIAL", [], message_sid="SM123")
    assert logged and logged[0][2] == "SM123"  # SID captured for swipe-reply correlation


async def test_question_is_answered_conversationally(harness, monkeypatch) -> None:
    from app.ai.qa import Answer

    monkeypatch.setattr(
        "app.ai.qa.answer_question",
        lambda *a, **k: _async(Answer(answer="We've published 4 posts this month.")),
    )
    await on_demand.handle_incoming_message(PHONE, "how many posts this month?", [])
    assert harness.sent_text and "4 posts" in harness.sent_text[-1][1]
    assert not harness.sent_media  # a text answer, no draft created
    assert harness.convos[PHONE]["current_post_id"] is None


async def test_question_referencing_a_post_resends_its_image(harness, monkeypatch) -> None:
    from app.ai.qa import Answer

    _seed_post(harness, "gulfood-1", caption="See us at Gulfood")
    monkeypatch.setattr(
        "app.ai.qa.answer_question",
        lambda *a, **k: _async(
            Answer(answer="Here's the Gulfood one.", referenced_post_id="gulfood-1")
        ),
    )
    await on_demand.handle_incoming_message(PHONE, "show me the gulfood one", [])
    assert harness.sent_media and harness.sent_media[-1][2] == "https://img.test/gulfood-1.png"


async def test_swipe_reply_reopens_old_post_for_edit(harness, monkeypatch) -> None:
    _seed_post(harness, "old-1")
    monkeypatch.setattr("app.messaging.history.by_sid", lambda sid: _async({"post_id": "old-1"}))
    await on_demand.handle_incoming_message(PHONE, "make it shorter", [], reply_to_sid="SMold")
    assert harness.convos[PHONE]["current_post_id"] == "old-1"  # re-targeted the old post
    assert harness.sent_media  # edited preview sent
    assert harness.convos[PHONE]["context"]["generated"]["caption"] == "Shorter."


async def test_swipe_reply_reposts_old_post_through_approval(harness, monkeypatch) -> None:
    _seed_post(harness, "old-2")
    monkeypatch.setattr("app.messaging.history.by_sid", lambda sid: _async({"post_id": "old-2"}))
    await on_demand.handle_incoming_message(PHONE, "approve", [], reply_to_sid="SMold2")
    assert harness.published == ["old-2"]  # re-opened then published (repost via approval)


async def test_swipe_reply_with_unknown_sid_degrades(harness, monkeypatch) -> None:
    # SID not in the log (>7 days / sandbox) → no re-open; falls back to normal handling.
    monkeypatch.setattr("app.messaging.history.by_sid", lambda sid: _async(None))
    await on_demand.handle_incoming_message(
        PHONE, "post about us at SIAL Paris", [], reply_to_sid="SMx"
    )
    assert len(harness.sent_media) == 1  # treated as a normal new request
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


# --- VHS video pipeline: Karen's video → HUD overlay composited → video post. ---

_VIDEO = [("https://media.twiliocdn.test/clip.mp4", "video/mp4")]


async def test_video_message_renders_vhs_and_previews(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "post this from the floor", _VIDEO)
    assert any("Processing your video" in body for _, body in harness.sent_text)  # interim
    assert harness.sent_media and harness.sent_media[-1][2].endswith(".mp4")  # video preview
    convo = harness.convos[PHONE]
    assert convo["state"] == "awaiting_approval"
    assert convo["context"]["treatment"] == "vhs_video"
    assert convo["context"]["media_url"].endswith(".mp4")


async def test_video_only_message_still_generates(harness) -> None:
    await on_demand.handle_incoming_message(PHONE, "", _VIDEO)  # no caption
    assert harness.sent_media and harness.sent_media[-1][2].endswith(".mp4")
    assert harness.convos[PHONE]["state"] == "awaiting_approval"


async def test_vhs_caption_edit_keeps_the_video(harness, monkeypatch) -> None:
    await on_demand.handle_incoming_message(PHONE, "post this from the floor", _VIDEO)
    composite_calls: list = []

    async def _fake_comp(*a, **k):
        composite_calls.append(1)
        return b"mp4bytes"

    monkeypatch.setattr("app.messaging.video.composite_vhs", _fake_comp)
    await on_demand.handle_incoming_message(PHONE, "make it shorter", [])
    assert not composite_calls  # a caption edit does NOT re-composite the video
    assert harness.convos[PHONE]["context"]["generated"]["caption"] == "Shorter."
    assert harness.sent_media[-1][2].endswith(".mp4")  # same clip re-sent


async def test_video_processing_failure_is_friendly(harness, monkeypatch) -> None:
    monkeypatch.setattr("app.messaging.video.composite_vhs", lambda *a, **k: _async(None))
    await on_demand.handle_incoming_message(PHONE, "post this clip", _VIDEO)
    assert any("couldn't process that video" in body for _, body in harness.sent_text)
    assert not harness.sent_media  # nothing sent on failure
