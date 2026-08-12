"""Calendar scheduler: source data, photo picking, slot adaptation, gating logic."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.ai.generator import GeneratedPost
from app.db import calendar_source
from app.templates.catalog import CALENDAR_TEMPLATE_ALIASES, TEMPLATES
from app.workflows import render_pipeline, scheduled

FINAL_VARIANTS = set(CALENDAR_TEMPLATE_ALIASES.values())


def _gp(**over) -> GeneratedPost:
    base = dict(
        caption="A caption",
        hashtags=["#Globex"],
        template_variant="ts_p2_cut_navyborder",
        headline="USAPEEC Americas Expo",
        subhead=None,
        figure=None,
        figure_unit=None,
        rationale="fits",
    )
    base.update(over)
    return GeneratedPost(**base)


# --------------------------------------------------------------------------- #
# calendar.json source
# --------------------------------------------------------------------------- #


def test_calendar_loads_156_unique_posts() -> None:
    entries = calendar_source.load_calendar()
    assert len(entries) == 156
    titles = {e.title for e in entries}
    assert len(titles) == 156  # zero repeats — client requirement


def test_calendar_uses_only_the_four_approved_templates() -> None:
    entries = calendar_source.load_calendar()
    assert {e.template for e in entries} == set(CALENDAR_TEMPLATE_ALIASES)


def test_event_ids_are_stable_and_unique() -> None:
    entries = calendar_source.load_calendar()
    ids = [e.event_id for e in entries]
    assert len(set(ids)) == 156
    assert ids[0] == entries[0].event_id  # deterministic across calls


def test_calendar_is_dormant_until_a_launch_date_is_set(monkeypatch) -> None:
    """No launch date -> nothing is ever due, so no post can be silently skipped."""
    monkeypatch.setattr(calendar_source, "launch_date", lambda: None)
    assert calendar_source.schedule() == []
    assert calendar_source.entries_due(date(2026, 8, 10), 3) == []


def test_entries_due_window(monkeypatch) -> None:
    launch = date(2026, 9, 7)  # a Monday
    monkeypatch.setattr(calendar_source, "launch_date", lambda: launch)
    due = calendar_source.entries_due(launch, 3)
    assert due
    assert all(launch <= e.post_date <= launch + timedelta(days=3) for e in due)
    assert not calendar_source.entries_due(date(2030, 1, 1), 3)


# --------------------------------------------------------------------------- #
# launch-date re-anchoring
# --------------------------------------------------------------------------- #


def test_launch_reflows_floating_posts_and_loses_none() -> None:
    """Evergreen posts survive any launch date; only past-dated anchors drop."""
    entries = calendar_source.load_calendar()
    floating = {e.title for e in entries if not e.anchored}
    for launch in (date(2026, 8, 10), date(2026, 9, 7), date(2027, 1, 4)):
        plan = calendar_source.schedule(launch)
        assert floating <= {e.title for e in plan}, launch
        assert all(e.post_date >= launch for e in plan), launch


def test_anchored_posts_keep_their_real_dates() -> None:
    """A holiday/show/anniversary can't be moved by a later launch."""
    plan = calendar_source.schedule(date(2026, 9, 7))
    for e in plan:
        if e.anchored:
            assert e.post_date == e.planned_date, e.title


def test_launch_drops_only_anchors_whose_moment_has_passed() -> None:
    launch = date(2026, 9, 7)
    dropped = calendar_source.dropped_by_launch(launch)
    assert all(e.anchored and e.planned_date < launch for e in dropped)
    titles = {e.title for e in calendar_source.schedule(launch)}
    assert not ({e.title for e in dropped} & titles)


def test_every_scheduled_post_lands_on_a_posting_day() -> None:
    """Mon/Wed/Fri only — Tue/Thu stay reserved for the video track."""
    plan = calendar_source.schedule(date(2026, 9, 7))
    assert {e.post_date.weekday() for e in plan} <= {0, 2, 4}


def test_no_two_posts_share_a_slot() -> None:
    plan = calendar_source.schedule(date(2026, 9, 7))
    dates = [e.post_date for e in plan]
    assert len(dates) == len(set(dates))


def test_event_ids_are_launch_independent() -> None:
    """Re-anchoring must not re-draft posts already sent for approval."""
    a = {e.title: e.event_id for e in calendar_source.schedule(date(2026, 8, 10))}
    b = {e.title: e.event_id for e in calendar_source.schedule(date(2027, 1, 4))}
    shared = a.keys() & b.keys()
    assert shared and all(a[t] == b[t] for t in shared)


def test_undrafted_filters_existing_posts(monkeypatch) -> None:
    entries = list(calendar_source.load_calendar()[:3])
    seen = {entries[0].event_id}
    monkeypatch.setattr(
        calendar_source.posts_db,
        "find_for_event",
        lambda event_id, event_type: {"id": "x"} if event_id in seen else None,
    )
    fresh = calendar_source.undrafted(entries)
    assert [e.title for e in fresh] == [e.title for e in entries[1:]]


# --------------------------------------------------------------------------- #
# photo pool
# --------------------------------------------------------------------------- #


def test_pick_photo_matches_subject_tags() -> None:
    entries = calendar_source.load_calendar()
    duck = next(e for e in entries if "duck" in e.title.lower())
    assert "duck" in scheduled.pick_photo(duck).name


def test_milestone_gets_placeholder_and_others_never_do() -> None:
    entries = calendar_source.load_calendar()
    milestone = next(e for e in entries if e.category == "milestone")
    assert scheduled.pick_photo(milestone).name.startswith("placeholder")
    for e in entries:
        if e.category != "milestone":
            assert not scheduled.pick_photo(e).name.startswith("placeholder")


def test_pick_photo_is_deterministic() -> None:
    entry = calendar_source.load_calendar()[0]
    assert scheduled.pick_photo(entry) == scheduled.pick_photo(entry)


# --------------------------------------------------------------------------- #
# render bridge for the finals
# --------------------------------------------------------------------------- #


def test_resolve_variant_accepts_calendar_names() -> None:
    for cal_name, variant in CALENDAR_TEMPLATE_ALIASES.items():
        assert render_pipeline.resolve_variant(cal_name) == variant
        assert TEMPLATES[variant].canvas == "portrait"


def test_middot_subhead_splits_into_two_tone_subline() -> None:
    post = _gp(subhead="18–20 March · Bogotá, Colombia")
    slots = render_pipeline.build_slots(post)
    render_pipeline._adapt_final_slots("ts_p2_cut_navyborder", slots, post)
    assert slots["subline_strong"] == "18–20 March"
    assert slots["subline_soft"] == "· Bogotá, Colombia"


def test_plain_subhead_stays_strong_only() -> None:
    post = _gp(subhead="One partner, global reach")
    slots = render_pipeline.build_slots(post)
    render_pipeline._adapt_final_slots("ts_p1_bolddip", slots, post)
    assert slots["subline_strong"] == "One partner, global reach"
    assert "subline_soft" not in slots


def test_ms_variant_maps_name_and_message() -> None:
    post = _gp(
        template_variant="ms_3_anniv_photo",
        headline="Lana Petrenko",
        subhead="26 years and counting. Thank you, Lana, for everything.",
    )
    slots = render_pipeline.build_slots(post)
    render_pipeline._adapt_final_slots("ms_3_anniv_photo", slots, post)
    assert slots["name"] == "Lana Petrenko"
    assert slots["message"].startswith("26 years")


# --------------------------------------------------------------------------- #
# category mapping + brief
# --------------------------------------------------------------------------- #


def test_every_calendar_category_has_a_prompt_family() -> None:
    entries = calendar_source.load_calendar()
    assert {e.category for e in entries} <= set(scheduled._CATEGORY_PROMPTS)


def test_entry_brief_carries_gist_and_purpose() -> None:
    entry = calendar_source.load_calendar()[0]
    brief = scheduled._entry_brief(entry)
    assert entry.gist in brief and entry.purpose in brief


# --------------------------------------------------------------------------- #
# the internal test run — every 2 hours, both testers, publish on approval
# --------------------------------------------------------------------------- #


def test_test_mode_previews_go_to_every_recipient(monkeypatch) -> None:
    """Abdul and Mike both need to see the post, or only one of them can answer."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APPROVAL_RECIPIENTS", "whatsapp:+111,whatsapp:+222")
    try:
        assert scheduled.approver_phones() == ["whatsapp:+111", "whatsapp:+222"]
    finally:
        get_settings.cache_clear()


def test_recipients_default_to_the_single_approver(monkeypatch) -> None:
    """Unset means production behaviour: one name, not the whole allowlist —
    dev/test numbers live in AUTHORIZED_NUMBERS and must not be sent client posts."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APPROVAL_RECIPIENTS", "")
    monkeypatch.setenv("AUTHORIZED_NUMBERS", "whatsapp:+111,whatsapp:+999")
    try:
        assert scheduled.approver_phones() == ["whatsapp:+111"]
    finally:
        get_settings.cache_clear()


def test_a_test_post_carries_todays_date_so_approval_publishes_now(monkeypatch) -> None:
    """A calendar post normally holds until its date. Approving a test post has to
    publish there and then, or nothing goes out for months."""
    captured: dict = {}

    async def fake_finalize(*_a, **kw):
        captured.update(kw)

    async def fake_generate(*_a, **_kw):
        return GeneratedPost(
            caption="c",
            hashtags=["#x"],
            template_variant="TS-p3-editorial_4x5",
            headline="h",
            rationale="r",
        )

    import app.workflows.on_demand as on_demand

    monkeypatch.setattr(on_demand, "_finalize_preview", fake_finalize)
    monkeypatch.setattr(scheduled.generator, "generate_post", fake_generate)

    entry = calendar_source.load_calendar()[0]
    asyncio.run(scheduled.draft_calendar_entry(entry, publish_today=True))

    assert captured["extra_render_meta"]["publish_on"] == date.today().isoformat()
    assert "Test post" in captured["caption_prefix"]


def test_a_normal_calendar_post_still_holds_for_its_date(monkeypatch) -> None:
    captured: dict = {}

    async def fake_finalize(*_a, **kw):
        captured.update(kw)

    async def fake_generate(*_a, **_kw):
        return GeneratedPost(
            caption="c",
            hashtags=["#x"],
            template_variant="TS-p3-editorial_4x5",
            headline="h",
            rationale="r",
        )

    import app.workflows.on_demand as on_demand

    monkeypatch.setattr(on_demand, "_finalize_preview", fake_finalize)
    monkeypatch.setattr(scheduled.generator, "generate_post", fake_generate)

    entry = calendar_source.load_calendar()[0]
    asyncio.run(scheduled.draft_calendar_entry(entry))

    assert captured["extra_render_meta"]["publish_on"] == entry.planned_date.isoformat()
    assert "Test post" not in captured["caption_prefix"]


def test_the_test_run_walks_the_approved_order(monkeypatch) -> None:
    """One post per interval, in the order the client signed off, skipping any
    already drafted — so a restart never re-sends what was already reviewed."""
    drafted: list[int] = []

    async def fake_draft(entry, *, publish_today=False):
        drafted.append(entry.seq)

    already = {calendar_source.load_calendar()[0].event_id}
    monkeypatch.setattr(scheduled, "draft_calendar_entry", fake_draft)
    monkeypatch.setattr(
        calendar_source.posts_db,
        "find_for_event",
        lambda event_id, event_type: {"id": "x"} if event_id in already else None,
    )

    assert asyncio.run(scheduled.draft_next_for_test()) is True
    assert drafted == [1]  # seq 0 was already drafted


def test_the_test_run_reports_when_the_calendar_is_exhausted(monkeypatch) -> None:
    """Returning False is what removes the job — otherwise it fires forever."""
    monkeypatch.setattr(
        calendar_source.posts_db, "find_for_event", lambda event_id, event_type: {"id": "x"}
    )
    assert asyncio.run(scheduled.draft_next_for_test()) is False


def test_one_unreachable_recipient_does_not_cost_the_others_their_preview(monkeypatch) -> None:
    """Mike's number fails until he joins the Twilio sandbox. That must not stop
    the post reaching Abdul — nor abort the drafting job that made it."""
    import app.workflows.on_demand as on_demand

    sent: list[str] = []

    async def flaky_send_media(to, _caption, _url, **_kw):
        if to == "whatsapp:+bad":
            raise RuntimeError("63015 not a sandbox participant")
        sent.append(to)
        return "SM1"

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(on_demand.twilio_client, "send_media", flaky_send_media)
    monkeypatch.setattr(on_demand.conversation, "transition", noop)
    monkeypatch.setattr(on_demand, "_apply_target", noop)
    monkeypatch.setattr(on_demand.approvals, "record", lambda *a, **kw: None)
    monkeypatch.setattr(on_demand.posts, "create", lambda **kw: {"id": "p1"})
    monkeypatch.setattr(on_demand.posts, "set_image_url", lambda *a, **kw: None)
    monkeypatch.setattr(on_demand.posts, "set_render_meta", lambda *a, **kw: None)

    async def fake_render(*_a, **_kw):
        return "https://example.test/i.png"

    monkeypatch.setattr(on_demand.render_pipeline, "render_and_store", fake_render)

    generated = GeneratedPost(
        caption="c",
        hashtags=["#x"],
        template_variant="TS-p3-editorial_4x5",
        headline="h",
        rationale="r",
    )
    asyncio.run(
        on_demand._finalize_preview(
            "whatsapp:+good",
            "brief",
            generated,
            recipients=["whatsapp:+bad", "whatsapp:+good"],
        )
    )
    assert sent == ["whatsapp:+good"]


def test_a_failed_render_releases_the_calendar_entry(monkeypatch) -> None:
    """A calendar entry counts as drafted the moment its post row exists. If the
    render then fails, leaving the row behind retires that entry for good —
    nobody saw it and nothing would ever draft it again."""
    import app.workflows.on_demand as on_demand

    deleted: list[str] = []

    async def boom(*_a, **_kw):
        raise RuntimeError("renderer not started")

    monkeypatch.setattr(on_demand.posts, "create", lambda **kw: {"id": "p1"})
    monkeypatch.setattr(on_demand.posts, "delete", lambda pid: deleted.append(pid))
    monkeypatch.setattr(on_demand.approvals, "record", lambda *a, **kw: None)
    monkeypatch.setattr(on_demand.render_pipeline, "render_and_store", boom)

    generated = GeneratedPost(
        caption="c",
        hashtags=["#x"],
        template_variant="TS-p3-editorial_4x5",
        headline="h",
        rationale="r",
    )
    with pytest.raises(RuntimeError):
        asyncio.run(on_demand._finalize_preview("whatsapp:+1", "brief", generated))

    assert deleted == ["p1"]


# --------------------------------------------------------------------------- #
# the test-run interval must survive a restart
# --------------------------------------------------------------------------- #


def test_the_test_grid_is_anchored_not_relative_to_boot() -> None:
    """A redeploy must not push the next post a whole interval into the future.

    This is the bug that silently swallowed two internal-test slots: an
    unanchored IntervalTrigger restarts its clock on every process start, so a
    day of deploys can skip posts with nothing in the logs. The anchored trigger
    lands on the same wall-clock grid no matter when the process booted.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.scheduler.automation import _test_grid

    tz = ZoneInfo("America/New_York")
    trigger = _test_grid(2.0, "America/New_York")

    # Booting at 08:26 must schedule 10:00 — not 10:26. The grid is anchored to
    # TODAY's midnight, so the boot time has to be today's too: pinning a literal
    # date here passes on the day it is written and fails every day after.
    today = datetime.now(tz).date()
    booted = datetime(today.year, today.month, today.day, 8, 26, tzinfo=tz)
    first = trigger.get_next_fire_time(None, booted)
    assert (first.hour, first.minute) == (10, 0)

    # And a boot ten minutes later lands on exactly the same slot.
    later = trigger.get_next_fire_time(None, booted + timedelta(minutes=10))
    assert later == first

    # Slots stay on even hours as the run continues.
    assert trigger.get_next_fire_time(first, first) == first + timedelta(hours=2)
