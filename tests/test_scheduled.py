"""Calendar scheduler: source data, photo picking, slot adaptation, gating logic."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app import clock
from app.ai.generator import GeneratedPost
from app.db import calendar_source
from app.templates.catalog import CALENDAR_TEMPLATE_ALIASES, TEMPLATES
from app.workflows import render_pipeline, scheduled

FINAL_VARIANTS = set(CALENDAR_TEMPLATE_ALIASES.values())
_HOUR = timedelta(hours=1)


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


def test_a_sequential_post_is_dated_tomorrow_so_it_holds_until_1am(monkeypatch) -> None:
    """The sequential run imitates the client cadence rather than short-circuiting
    it: drafted today, dated tomorrow, so approving parks it until 1am like every
    other scheduled post. It used to carry today's date and publish on approval,
    which demonstrated a flow the client will never see."""
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
    asyncio.run(scheduled.draft_calendar_entry(entry, in_sequence=True))

    tomorrow = clock.today() + timedelta(days=1)
    assert captured["extra_render_meta"]["publish_on"] == tomorrow.isoformat()
    assert not clock.is_due(tomorrow)  # approving it today cannot publish it
    assert "1am" in captured["caption_prefix"]


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
    assert "Post 1/" not in captured["caption_prefix"]


def test_the_test_run_walks_the_approved_order(monkeypatch) -> None:
    """One post per interval, in the order the client signed off, skipping any
    already drafted — so a restart never re-sends what was already reviewed."""
    drafted: list[int] = []

    async def fake_draft(entry, *, in_sequence=False):
        drafted.append(entry.seq)

    already = {calendar_source.load_calendar()[0].event_id}
    monkeypatch.setattr(scheduled, "draft_calendar_entry", fake_draft)
    monkeypatch.setattr(
        calendar_source.posts_db,
        "find_for_event",
        lambda event_id, event_type: {"id": "x"} if event_id in already else None,
    )

    assert asyncio.run(scheduled.draft_next_in_sequence()) is True
    assert drafted == [1]  # seq 0 was already drafted


def test_the_draft_window_reaches_the_next_working_day(monkeypatch) -> None:
    """Friday's 7am run must cover Saturday, Sunday AND Monday: a Monday post
    previewed on Sunday is a post nobody is at work to approve."""
    windows: list[tuple[date, int]] = []
    monkeypatch.setattr(
        calendar_source, "entries_due", lambda today, lead: windows.append((today, lead)) or []
    )

    asyncio.run(scheduled.draft_due_posts(today=date(2026, 8, 14)))  # a Friday
    asyncio.run(scheduled.draft_due_posts(today=date(2026, 8, 17)))  # a Monday

    assert windows == [(date(2026, 8, 14), 3), (date(2026, 8, 17), 1)]


def test_drafted_since_sees_only_calendar_posts(monkeypatch) -> None:
    """What the boot catch-up asks: did today's slot actually send anything? An
    on-demand post Karen asked for is not an answer to that question."""
    slot = clock.now() - timedelta(hours=2)
    rows = [
        {"event_type": None, "created_at": (slot + timedelta(minutes=5)).isoformat()},
        {"event_type": calendar_source.EVENT_TYPE, "created_at": (slot - _HOUR).isoformat()},
    ]
    monkeypatch.setattr(scheduled.posts, "recent", lambda limit=40: rows)
    assert asyncio.run(scheduled.drafted_since(slot)) is False

    rows.append(
        {"event_type": calendar_source.EVENT_TYPE, "created_at": (slot + _HOUR).isoformat()}
    )
    assert asyncio.run(scheduled.drafted_since(slot)) is True


def test_the_sequential_run_reports_when_the_calendar_is_exhausted(monkeypatch) -> None:
    """Returning False is what removes the job — otherwise it fires forever."""
    monkeypatch.setattr(
        calendar_source.posts_db, "find_for_event", lambda event_id, event_type: {"id": "x"}
    )
    assert asyncio.run(scheduled.draft_next_in_sequence()) is False


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


def test_an_explicit_start_suppresses_every_earlier_slot() -> None:
    """TEST_START_AT lines the run up with the moment Twilio's cap frees capacity.

    Anchored to midnight instead, an hourly grid fires into the closed window and
    drafts posts nobody can receive — the waste this setting exists to avoid.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.scheduler.automation import _test_grid

    tz = ZoneInfo("America/New_York")
    trigger = _test_grid(1.0, "America/New_York", "2026-08-12T12:00")

    # 10:47, well before the anchor: the next slot is the anchor itself, not 11:00.
    booted = datetime(2026, 8, 12, 10, 47, tzinfo=tz)
    first = trigger.get_next_fire_time(None, booted)
    assert first == datetime(2026, 8, 12, 12, 0, tzinfo=tz)

    # Hourly from there on.
    assert trigger.get_next_fire_time(first, first) == first + timedelta(hours=1)


def test_an_unparseable_start_falls_back_to_midnight_rather_than_stopping() -> None:
    """A typo in an env var must not take the whole run down — a scheduler that
    never fires is a worse failure than one that fires an hour early."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.scheduler.automation import _test_grid

    tz = ZoneInfo("America/New_York")
    trigger = _test_grid(1.0, "America/New_York", "noon-ish")

    today = datetime.now(tz).date()
    booted = datetime(today.year, today.month, today.day, 10, 47, tzinfo=tz)
    first = trigger.get_next_fire_time(None, booted)
    assert (first.hour, first.minute) == (11, 0)


def test_a_daily_grid_survives_a_deploy_and_still_lands_at_7am() -> None:
    """Anchored at 7am, a restart at any hour schedules the NEXT 7am — never an
    interval counted from the deploy, which is how a redeploy used to walk the
    slot forward through the day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.scheduler.automation import _test_grid

    tz = ZoneInfo("America/New_York")
    trigger = _test_grid(24.0, "America/New_York", "2026-08-14T07:00")

    redeployed = datetime(2026, 8, 15, 15, 20, tzinfo=tz)
    assert trigger.get_next_fire_time(None, redeployed) == datetime(2026, 8, 16, 7, 0, tzinfo=tz)


def test_the_boot_catch_up_runs_a_slot_the_restart_would_have_eaten(monkeypatch) -> None:
    """The gap the catch-up closes: APScheduler recomputes the next fire time from
    now, so a deploy at 9am on a 7am daily grid schedules TOMORROW and today's post
    is lost. Nothing else notices — the log looks healthy."""
    from types import SimpleNamespace

    from app.scheduler import automation

    ran: list[str] = []
    # Relative to now, not a wall-clock hour: pinned to "tomorrow 7am" this test
    # only passed when it happened to be run after 7am.
    next_run = clock.now() + timedelta(hours=1)

    monkeypatch.setattr(
        automation,
        "_scheduler",
        SimpleNamespace(get_job=lambda _id: SimpleNamespace(next_run_time=next_run)),
    )
    monkeypatch.setattr(
        automation,
        "get_settings",
        lambda: SimpleNamespace(test_interval_hours=24.0, timezone="America/New_York"),
    )

    async def fake_sequence() -> None:
        ran.append("drafted")

    monkeypatch.setattr(automation, "_sequence_job", fake_sequence)

    # Today's 7am slot passed with nothing drafted -> the catch-up fires it.
    monkeypatch.setattr(automation.scheduled, "drafted_since", lambda _m: _true(False))
    asyncio.run(automation._catch_up_job())
    assert ran == ["drafted"]

    # Already drafted since that slot -> nothing happens, no double post.
    ran.clear()
    monkeypatch.setattr(automation.scheduled, "drafted_since", lambda _m: _true(True))
    asyncio.run(automation._catch_up_job())
    assert ran == []


async def _true(value: bool) -> bool:
    return value
