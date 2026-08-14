"""Guided 4-question intake + scheduled approval-hold/publish (offline fakes)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app import clock
from app.ai.generator import GeneratedPost
from app.ai.intent import Intent, IntentType
from app.workflows import approval, intake, scheduled

PHONE = "whatsapp:+19170001111"

_DRAFT = GeneratedPost(
    caption="Premium duck, retail-ready.",
    hashtags=["#Globex"],
    template_variant="promotional",  # model's pick — intake must override it
    headline="Premium Duck",
    subhead="Retail-ready.",
    rationale="fits",
)


def _async(value=None):
    async def coro(*a, **k):
        return value

    return coro()


@pytest.fixture
def world(monkeypatch):
    convos: dict[str, dict] = {}
    posts_store: dict[str, dict] = {}
    sent_text: list[str] = []
    sent_media: list[str] = []
    published: list[str] = []
    counter = {"n": 0}

    def _blank(phone):
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

    async def classify(message, state, memory=None):
        if message.strip().lower() in ("cancel", "forget it"):
            return Intent(type=IntentType.cancellation, confidence=0.95)
        return Intent(type=IntentType.unclear, confidence=0.4)

    async def fake_publish(pid):
        published.append(pid)
        return {}

    monkeypatch.setattr("app.messaging.conversation.get_or_create", get_or_create)
    monkeypatch.setattr("app.messaging.conversation.transition", transition)
    monkeypatch.setattr("app.messaging.conversation.clear_post", clear_post)
    monkeypatch.setattr("app.db.posts.create", create)
    monkeypatch.setattr("app.db.posts.get", lambda pid: posts_store.get(pid))
    monkeypatch.setattr(
        "app.db.posts.set_status", lambda pid, s: posts_store[pid].update({"status": s})
    )
    monkeypatch.setattr(
        "app.db.posts.set_image_url", lambda pid, u: posts_store[pid].update({"image_url": u})
    )
    monkeypatch.setattr(
        "app.db.posts.set_render_meta",
        lambda pid, m: posts_store[pid].update({"render_meta": m}),
    )
    monkeypatch.setattr(
        "app.db.posts.list_by_status",
        lambda s: [p for p in posts_store.values() if p.get("status") == s],
    )
    monkeypatch.setattr("app.db.approvals.record", lambda *a, **k: {"id": "ah"})
    monkeypatch.setattr("app.ai.intent.classify_intent", classify)
    monkeypatch.setattr("app.ai.generator.generate_freeform", lambda *a, **k: _async(_DRAFT))
    monkeypatch.setattr(
        "app.workflows.render_pipeline.render_and_store",
        lambda post_id, post, **k: _async(f"https://img.test/{post_id}.png"),
    )
    monkeypatch.setattr(
        "app.messaging.twilio_client.send_text",
        lambda to, body, **k: _async(sent_text.append(body)),
    )
    monkeypatch.setattr(
        "app.messaging.twilio_client.send_media",
        lambda to, body, url, **k: _async(sent_media.append(body)),
    )
    monkeypatch.setattr(
        "app.messaging.media.download_twilio_media",
        lambda url, **k: _async((b"attached-bytes", "image/jpeg")),
    )
    monkeypatch.setattr("app.publishing.publisher.publish_post", fake_publish)
    monkeypatch.setattr(
        "app.workflows.scheduled.get_settings",
        lambda: SimpleNamespace(authorized_numbers_list=[PHONE]),
    )
    return SimpleNamespace(
        convos=convos,
        posts=posts_store,
        sent_text=sent_text,
        sent_media=sent_media,
        published=published,
    )


def _convo(world):
    return world.convos[PHONE]


# --------------------------------------------------------------------------- #
# intake flow
# --------------------------------------------------------------------------- #


async def test_thin_brief_starts_intake_and_substantial_brief_does_not(world) -> None:
    assert not await intake.maybe_start(PHONE, "post about our duck retail bags in Asia", None)
    assert await intake.maybe_start(PHONE, "new post", None)
    assert _convo(world)["state"] == "intake"
    assert world.sent_text[-1] == intake.Q_ABOUT


async def test_photo_attached_skips_the_questionnaire(world) -> None:
    assert not await intake.maybe_start(PHONE, "new post", ("http://m/1", "image/jpeg"))


async def test_full_walkthrough_forces_template_and_previews(world) -> None:
    await intake.maybe_start(PHONE, "new post", None)
    await intake.handle_answer(PHONE, _convo(world), "Our premium duck line", None)
    assert world.sent_text[-1] == intake.Q_WHY
    await intake.handle_answer(PHONE, _convo(world), "Retail buyers should ask for samples", None)
    assert world.sent_text[-1] == intake.Q_PHOTO
    await intake.handle_answer(PHONE, _convo(world), "stock", None)
    assert world.sent_text[-1] == intake.Q_TEMPLATE
    await intake.handle_answer(PHONE, _convo(world), "2", None)

    post = world.posts["post-1"]
    assert post["template_type"] == "ts_p2_cut_navyborder"  # forced, not the model's pick
    assert post["status"] == "pending_approval"
    assert world.sent_media, "preview must be sent"
    assert _convo(world)["state"] == "awaiting_approval"
    assert _convo(world)["context"].get("intake") is None


async def test_seeded_about_skips_first_question(world) -> None:
    await intake.maybe_start(PHONE, "post about duck", None)  # thin but has a subject
    assert world.sent_text[-1] == intake.Q_WHY


async def test_photo_mid_flow_answers_the_picture_question(world) -> None:
    await intake.maybe_start(PHONE, "new post", None)
    await intake.handle_answer(PHONE, _convo(world), "Gulfood recap", ("http://m/9", "image/jpeg"))
    # about answered by text AND photo captured -> next question is template, not photo
    assert world.sent_text[-1] == intake.Q_WHY
    await intake.handle_answer(PHONE, _convo(world), "Thank partners", None)
    assert world.sent_text[-1] == intake.Q_TEMPLATE
    await intake.handle_answer(PHONE, _convo(world), "auto", None)
    assert world.posts["post-1"]["template_type"] == "ts_p1_bolddip"  # gulfood -> show pick


async def test_cancel_mid_intake_returns_to_idle(world) -> None:
    await intake.maybe_start(PHONE, "new post", None)
    await intake.handle_answer(PHONE, _convo(world), "cancel", None)
    assert _convo(world)["state"] == "idle"
    assert _convo(world)["context"].get("intake") is None
    assert world.sent_text[-1] == intake.CANCELLED


# --------------------------------------------------------------------------- #
# approval hold + scheduled publish
# --------------------------------------------------------------------------- #


def _pending_post(world, publish_on: str | None):
    from app.db import posts as posts_db

    post = posts_db.create(caption="c", status="pending_approval")
    if publish_on:
        world.posts[post["id"]]["render_meta"] = {"publish_on": publish_on}
    return post["id"]


async def test_approving_future_scheduled_post_holds_publication(world) -> None:
    future = (clock.today() + timedelta(days=2)).isoformat()
    pid = _pending_post(world, future)
    convo = {"phone_number": PHONE, "current_post_id": pid, "context": {}}
    await approval.handle_approval(PHONE, convo)
    assert world.posts[pid]["status"] == "approved"
    assert world.published == []  # held for the calendar date
    assert "go out automatically" in world.sent_text[-1]


def _freeze(monkeypatch, moment: datetime) -> None:
    """Pin the client's wall clock, so a boundary test is not a flaky one."""
    monkeypatch.setattr(clock, "now", lambda: moment)


async def test_evening_approval_still_holds_until_1am(world, monkeypatch) -> None:
    """9pm in New York is already tomorrow in UTC — the server's day, not the
    client's. Comparing dates on the server clock released the hold and published
    four hours early; the hold is against the 1am moment itself."""
    tomorrow = clock.today() + timedelta(days=1)
    _freeze(monkeypatch, datetime.combine(tomorrow - timedelta(days=1), time(21), clock.tz()))
    pid = _pending_post(world, tomorrow.isoformat())
    convo = {"phone_number": PHONE, "current_post_id": pid, "context": {}}
    await approval.handle_approval(PHONE, convo)
    assert world.published == []
    assert "1am" in world.sent_text[-1]


async def test_approval_after_the_slot_publishes_at_once(world, monkeypatch) -> None:
    """A yes given on the day, after the 1am sweep has already run, must not wait
    another 24 hours for the next one."""
    today = clock.today()
    _freeze(monkeypatch, datetime.combine(today, time(9), clock.tz()))
    pid = _pending_post(world, today.isoformat())
    convo = {"phone_number": PHONE, "current_post_id": pid, "context": {}}
    await approval.handle_approval(PHONE, convo)
    assert world.published == [pid]


async def test_approval_at_midnight_on_the_day_still_waits_for_1am(world, monkeypatch) -> None:
    today = clock.today()
    _freeze(monkeypatch, datetime.combine(today, time(0, 30), clock.tz()))
    pid = _pending_post(world, today.isoformat())
    convo = {"phone_number": PHONE, "current_post_id": pid, "context": {}}
    await approval.handle_approval(PHONE, convo)
    assert world.published == []


async def test_a_test_run_post_publishes_on_approval_whatever_the_hour(world, monkeypatch) -> None:
    """The internal run's promise is "publishes as soon as you approve". Before
    1am the calendar gate would otherwise hold it for an hour."""
    today = clock.today()
    _freeze(monkeypatch, datetime.combine(today, time(0, 30), clock.tz()))
    pid = _pending_post(world, today.isoformat())
    world.posts[pid]["render_meta"]["publish_now"] = True
    convo = {"phone_number": PHONE, "current_post_id": pid, "context": {}}
    await approval.handle_approval(PHONE, convo)
    assert world.published == [pid]


async def test_approving_ondemand_post_publishes_immediately(world) -> None:
    pid = _pending_post(world, None)
    convo = {"phone_number": PHONE, "current_post_id": pid, "context": {}}
    await approval.handle_approval(PHONE, convo)
    assert world.published == [pid]


async def test_publish_due_posts_fires_only_on_the_day(world) -> None:
    due = _pending_post(world, clock.today().isoformat())
    future = _pending_post(world, (clock.today() + timedelta(days=3)).isoformat())
    for pid in (due, future):
        world.posts[pid]["status"] = "approved"
    count = await scheduled.publish_due_posts()
    assert count == 1
    assert world.published == [due]
    assert world.posts[future]["status"] == "approved"  # untouched
