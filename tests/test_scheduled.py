"""Calendar scheduler: source data, photo picking, slot adaptation, gating logic."""

from __future__ import annotations

from datetime import date

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


def test_entries_due_window() -> None:
    entries = calendar_source.load_calendar()
    first = min(e.post_date for e in entries)
    due = calendar_source.entries_due(first, 3)
    assert due and all(first <= e.post_date <= first.replace(day=first.day + 3) for e in due)
    assert not calendar_source.entries_due(date(2030, 1, 1), 3)


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
