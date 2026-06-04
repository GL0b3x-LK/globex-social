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

from app.ai.generator import GeneratedPost
from app.ai.intent import Intent, IntentType
from app.messaging.transcription import Outcome, Transcript
from app.workflows import on_demand

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


async def _fake_classify(message: str, state) -> Intent:
    m = message.lower()
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
        lambda url: _async((b"fake-image-bytes", "image/jpeg")),
    )
    monkeypatch.setattr(
        "app.workflows.render_pipeline.render_and_store",
        lambda post_id, post, **k: _async(f"https://img.test/{post_id}.png"),
    )
    monkeypatch.setattr(
        "app.messaging.twilio_client.send_text",
        lambda to, body: _async(sent_text.append((to, body))),
    )
    monkeypatch.setattr(
        "app.messaging.twilio_client.send_media",
        lambda to, body, url: _async(sent_media.append((to, body, url))),
    )
    monkeypatch.setattr(
        "app.publishing.publisher.publish_post", lambda pid: _async(published.append(pid))
    )
    monkeypatch.setattr(
        "app.workflows.on_demand.get_settings",
        lambda: SimpleNamespace(authorized_numbers_list=[PHONE]),
    )
    return SimpleNamespace(
        convos=convos,
        posts=posts_store,
        sent_text=sent_text,
        sent_media=sent_media,
        published=published,
    )


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
