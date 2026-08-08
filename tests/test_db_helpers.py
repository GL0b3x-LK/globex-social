"""Integration CRUD tests for every db helper, against the dev Supabase.

Each test creates clearly-namespaced rows and cleans them up in a finally block,
so the suite is safe to run repeatedly and doesn't pollute reference data.
"""

from __future__ import annotations

import pytest

from app.db import (
    approvals,
    conversations,
    employees,
    holidays,
    packaging_rotation,
    posts,
    trade_shows,
)
from app.db.client import get_supabase

pytestmark = pytest.mark.usefixtures("supabase_ready")

TEST_PHONE = "whatsapp:+10000000001"
TEST_EMP = "__pytest_employee__"
TEST_HOL = "__pytest_holiday__"
TEST_SHOW = "__pytest_show__"
TEST_SLOTS = [19, 20]
TEST_EVENT_ID = "00000000-0000-0000-0000-0000000000aa"


def _raw_delete(table: str, col: str, val) -> None:
    get_supabase().table(table).delete().eq(col, val).execute()


def test_employees_crud():
    employees.upsert_many(
        [{"name": TEST_EMP, "title": "QA", "hire_date": "2000-01-01", "active": True}]
    )
    try:
        row = employees.get_by_name(TEST_EMP)
        assert row and row["title"] == "QA"
        assert any(e["name"] == TEST_EMP for e in employees.list_active())
        cands = employees.milestone_candidates(min_years=20, as_of_year=2026)
        assert any(c["name"] == TEST_EMP and c["years"] == 26 for c in cands)
        # idempotent re-upsert (update by name)
        employees.upsert_many([{"name": TEST_EMP, "title": "QA2", "active": True}])
        assert employees.get_by_name(TEST_EMP)["title"] == "QA2"
    finally:
        _raw_delete("employees", "name", TEST_EMP)
    assert employees.get_by_name(TEST_EMP) is None


def test_holidays_crud():
    holidays.upsert_many(
        [
            {
                "name": TEST_HOL,
                "month": "January",
                "date_2026": "2026-01-10",
                "date_2027": "2027-01-10",
                "is_month_long": False,
                "category": "food_industry",
                "recurring": True,
            }
        ]
    )
    try:
        row = holidays.get_by_name(TEST_HOL)
        assert row and row["category"] == "food_industry"
        assert any(h["name"] == TEST_HOL for h in holidays.by_month("January"))
    finally:
        _raw_delete("holidays", "name", TEST_HOL)
    assert holidays.get_by_name(TEST_HOL) is None


def test_trade_shows_crud():
    trade_shows.upsert_many(
        [
            {
                "name": TEST_SHOW,
                "month": "April",
                "location": "Test City",
                "hidden": False,
                "needs_date_confirmation": True,
            }
        ]
    )
    try:
        row = trade_shows.get_by_name(TEST_SHOW)
        assert row and row["needs_date_confirmation"] is True
        assert any(s["name"] == TEST_SHOW for s in trade_shows.needs_confirmation())
        assert any(s["name"] == TEST_SHOW for s in trade_shows.list_visible())
    finally:
        _raw_delete("trade_shows", "name", TEST_SHOW)
    assert trade_shows.get_by_name(TEST_SHOW) is None


def test_posts_and_approvals_crud():
    post = posts.create(
        content="body",
        caption="cap",
        hashtags=["#globex"],
        template_type="stats",
        status="draft",
    )
    pid = post["id"]
    try:
        assert posts.get(pid)["caption"] == "cap"
        posts.set_status(pid, "pending_approval")
        assert posts.get(pid)["status"] == "pending_approval"
        posts.set_status(pid, "approved")
        approved = posts.get(pid)
        assert approved["status"] == "approved" and approved["approved_at"]
        posts.set_image_url(pid, "https://example.com/x.png")
        assert posts.get(pid)["image_url"].endswith("x.png")
        assert any(p["id"] == pid for p in posts.list_by_status("approved"))

        approvals.record(pid, "approved")
        approvals.record(pid, "edit_requested", "make it shorter")
        hist = approvals.history(pid)
        assert len(hist) == 2 and hist[0]["action"] == "approved"

        # Scheduler idempotency lookup.
        ev = posts.create(template_type="holiday", event_id=TEST_EVENT_ID, event_type="holiday")
        try:
            found = posts.find_for_event(TEST_EVENT_ID, "holiday")
            assert found and found["id"] == ev["id"]
        finally:
            posts.delete(ev["id"])
    finally:
        posts.delete(pid)
    # Cascade also removed approval_history rows.
    assert posts.get(pid) is None
    assert approvals.history(pid) == []


def test_conversations_crud():
    conversations.delete(TEST_PHONE)
    try:
        created = conversations.get_or_create(TEST_PHONE)
        assert created["state"] == "idle"
        conversations.transition(TEST_PHONE, state="awaiting_approval", context_patch={"draft": 1})
        c2 = conversations.get(TEST_PHONE)
        assert c2["state"] == "awaiting_approval" and c2["context"]["draft"] == 1
        # context_patch merges, doesn't replace
        conversations.transition(TEST_PHONE, context_patch={"extra": "x"})
        c3 = conversations.get(TEST_PHONE)
        assert c3["context"] == {"draft": 1, "extra": "x"}
        conversations.clear_post(TEST_PHONE)
        assert conversations.get(TEST_PHONE)["current_post_id"] is None
    finally:
        conversations.delete(TEST_PHONE)
    assert conversations.get(TEST_PHONE) is None


def test_packaging_rotation_crud():
    packaging_rotation.upsert_many(
        [
            {
                "slot_number": s,
                "caption_template": f"slot {s}",
                "image_asset_path": f"x{s}.png",
                "active": True,
            }
            for s in TEST_SLOTS
        ]
    )
    try:
        active_slots = {r["slot_number"] for r in packaging_rotation.list_active()}
        assert set(TEST_SLOTS).issubset(active_slots)
        assert packaging_rotation.next_slot() is not None  # never-posted slots exist
        packaging_rotation.mark_posted(TEST_SLOTS[0])
        marked = next(
            r for r in packaging_rotation.list_active() if r["slot_number"] == TEST_SLOTS[0]
        )
        assert marked["last_posted_at"] is not None
    finally:
        for s in TEST_SLOTS:
            _raw_delete("branded_packaging_rotation", "slot_number", s)
