"""Previews that were built but never arrived.

The failure this covers: Mike's industrial-freezer image was generated, stored
and rendered at 16:39 on 2026-08-11, then the send hit Twilio's 50-a-day cap.
The error went to the log, the post sat finished in the database, and nobody
ever saw it. Work completing is not the same as work arriving.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflows import redelivery


class _Fake:
    """A posts table just real enough: reads see writes."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {r["id"]: r for r in rows}
        self.sent: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    def get(self, pid: str) -> dict[str, Any] | None:
        row = self.rows.get(pid)
        return dict(row) if row else None

    def set_render_meta(self, pid: str, meta: dict[str, Any]) -> dict[str, Any]:
        self.rows[pid]["render_meta"] = meta
        return {}

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows.values() if r.get("status") == status]

    async def send_media(self, to: str, body: str, media_url: str, **kw: Any) -> str | None:
        if to in self.fail:
            return None
        self.sent.append((to, str(kw.get("post_id"))))
        return "MM1"


def _post(pid: str, owed: list[str] | None = None, status: str = "pending_approval") -> dict:
    meta: dict[str, Any] = {"calendar": {"title": f"post {pid}"}}
    if owed:
        meta["undelivered"] = owed
    return {
        "id": pid,
        "status": status,
        "caption": f"caption for {pid}",
        "image_url": f"https://cdn.test/{pid}.png",
        "created_at": f"2026-08-11T0{pid[-1]}:00:00+00:00",
        "render_meta": meta,
    }


@pytest.fixture()
def wired(monkeypatch) -> _Fake:
    fake = _Fake([])

    monkeypatch.setattr(redelivery.posts, "get", fake.get)
    monkeypatch.setattr(redelivery.posts, "set_render_meta", fake.set_render_meta)
    monkeypatch.setattr(redelivery.posts, "list_by_status", fake.list_by_status)
    # The retry goes out via send_preview now: the window is shut by definition
    # when a preview is owed, so it must be able to fall back to the template.
    monkeypatch.setattr(redelivery.twilio_client, "try_send_preview", fake.send_media)
    return fake


@pytest.mark.asyncio
async def test_a_failed_send_is_recorded_against_the_post(wired: _Fake) -> None:
    wired.rows["p1"] = _post("p1")
    await redelivery.record("p1", "whatsapp:+44", delivered=False)
    assert wired.rows["p1"]["render_meta"]["undelivered"] == ["whatsapp:+44"]


@pytest.mark.asyncio
async def test_a_successful_send_clears_the_claim(wired: _Fake) -> None:
    wired.rows["p1"] = _post("p1", owed=["whatsapp:+44"])
    await redelivery.record("p1", "whatsapp:+44", delivered=True)
    assert "undelivered" not in wired.rows["p1"]["render_meta"]


@pytest.mark.asyncio
async def test_the_same_recipient_is_not_owed_twice(wired: _Fake) -> None:
    wired.rows["p1"] = _post("p1", owed=["whatsapp:+44"])
    await redelivery.record("p1", "whatsapp:+44", delivered=False)
    assert wired.rows["p1"]["render_meta"]["undelivered"] == ["whatsapp:+44"]


@pytest.mark.asyncio
async def test_one_recipients_failure_does_not_clear_anothers(wired: _Fake) -> None:
    wired.rows["p1"] = _post("p1", owed=["whatsapp:+44", "whatsapp:+234"])
    await redelivery.record("p1", "whatsapp:+44", delivered=True)
    assert wired.rows["p1"]["render_meta"]["undelivered"] == ["whatsapp:+234"]


@pytest.mark.asyncio
async def test_retry_sends_what_was_owed_and_clears_it(wired: _Fake) -> None:
    wired.rows["p1"] = _post("p1", owed=["whatsapp:+44"])
    sent = await redelivery.retry_undelivered()
    assert sent == 1
    assert wired.sent == [("whatsapp:+44", "p1")]
    assert "undelivered" not in wired.rows["p1"]["render_meta"]


@pytest.mark.asyncio
async def test_posts_that_moved_on_are_never_resurfaced(wired: _Fake) -> None:
    """An approved or published post is no longer awaiting anyone's eyes —
    pushing it back into the chat would read as a new thing needing a decision."""
    wired.rows["p1"] = _post("p1", owed=["whatsapp:+44"], status="published")
    wired.rows["p2"] = _post("p2", owed=["whatsapp:+44"], status="cancelled")
    assert await redelivery.retry_undelivered() == 0
    assert wired.sent == []


@pytest.mark.asyncio
async def test_a_still_blocked_account_stops_the_sweep(wired: _Fake) -> None:
    """The usual cause is account-wide (closed window, exhausted quota), so
    carrying on would burn the rest of the day's quota re-failing."""
    wired.rows["p1"] = _post("p1", owed=["whatsapp:+44"])
    wired.rows["p2"] = _post("p2", owed=["whatsapp:+44"])
    wired.fail.add("whatsapp:+44")
    assert await redelivery.retry_undelivered() == 0
    # both claims survive for the next pass
    assert wired.rows["p1"]["render_meta"]["undelivered"] == ["whatsapp:+44"]
    assert wired.rows["p2"]["render_meta"]["undelivered"] == ["whatsapp:+44"]


@pytest.mark.asyncio
async def test_a_post_with_no_render_is_not_chased(wired: _Fake) -> None:
    row = _post("p1", owed=["whatsapp:+44"])
    row["image_url"] = None
    wired.rows["p1"] = row
    assert await redelivery.retry_undelivered() == 0


@pytest.mark.asyncio
async def test_bookkeeping_failure_never_breaks_the_turn(wired: _Fake, monkeypatch) -> None:
    """Recording is a courtesy to a later job; it must not take down the edit
    that just succeeded."""

    def boom(pid: str) -> dict[str, Any]:
        raise RuntimeError("supabase down")

    monkeypatch.setattr(redelivery.posts, "get", boom)
    await redelivery.record("p1", "whatsapp:+44", delivered=False)  # does not raise


# --------------------------------------------------------------------------- #
# the async-failure gap: Twilio accepts, then fails out of band
# --------------------------------------------------------------------------- #


async def test_a_failed_status_callback_queues_the_post_for_redelivery(monkeypatch) -> None:
    """The hole that hid three days of undelivered posts. Twilio returned 201 +
    SID for each one and failed them afterwards (63016/63015); the code treated
    the SID as proof of delivery, so `undelivered` was never set and the retry
    job had nothing to chase."""
    from app.messaging import webhook

    recorded: list[tuple[str, str, bool]] = []

    async def fake_by_sid(sid):
        return {"post_id": "post-1", "phone_number": "whatsapp:+447877178815"}

    async def fake_record(post_id, phone, *, delivered):
        recorded.append((post_id, phone, delivered))

    monkeypatch.setattr(webhook.history, "by_sid", fake_by_sid)
    monkeypatch.setattr(webhook.redelivery, "record", fake_record)

    class _Req:
        async def form(self):
            return {"MessageSid": "SM1", "MessageStatus": "failed", "ErrorCode": "63015"}

    await webhook.status_callback(_Req(), None)
    assert recorded == [("post-1", "whatsapp:+447877178815", False)]


async def test_a_delivered_status_callback_changes_nothing(monkeypatch) -> None:
    from app.messaging import webhook

    recorded: list = []
    monkeypatch.setattr(webhook.redelivery, "record", lambda *a, **k: recorded.append(a) or _noop())

    class _Req:
        async def form(self):
            return {"MessageSid": "SM1", "MessageStatus": "delivered"}

    await webhook.status_callback(_Req(), None)
    assert recorded == []


async def _noop() -> None:
    return None
