"""The sheet's "Exact Caption" contract: client words in, posted words back.

Two promises under test: anything the client typed in that column posts
VERBATIM (nothing appended, nothing rewritten), and after publishing, the cell
holds exactly the caption that went to Instagram. And in both directions, a
missing or broken sheet changes nothing about posting.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.generator import GeneratedPost
from app.db.calendar_source import EVENT_TYPE, load_calendar
from app.publishing import calendar_sheet, publisher
from app.publishing.blotato import PublishResult
from app.publishing.platforms import Platform
from app.workflows import scheduled


@pytest.mark.asyncio
async def test_unconfigured_bridge_is_a_silent_no_op(monkeypatch) -> None:
    monkeypatch.setenv("SHEET_WEBAPP_URL", "")
    monkeypatch.setenv("SHEET_WEBAPP_SECRET", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert await calendar_sheet.exact_caption("Whole bird, export grade") is None
        assert await calendar_sheet.write_back("Whole bird, export grade", "x") is False
    finally:
        get_settings.cache_clear()


def _generated(**over: Any) -> GeneratedPost:
    base: dict[str, Any] = dict(
        caption="AI caption",
        hashtags=["#Globex"],
        template_variant="ts_p2_cut_navyborder",
        headline="H",
        rationale="r",
    )
    base.update(over)
    return GeneratedPost(**base)


@pytest.fixture()
def drafted(monkeypatch):
    """Run draft_calendar_entry with everything faked except the sheet hook."""
    captured: dict[str, Any] = {}

    async def fake_generate(*a, **kw):
        return _generated()

    async def fake_finalize(*_a, **kw):
        captured.update(kw)
        captured["generated"] = _a[2] if len(_a) > 2 else kw.get("generated")

    import app.workflows.on_demand as on_demand

    monkeypatch.setattr(scheduled.generator, "generate_post", fake_generate)
    monkeypatch.setattr(on_demand, "_finalize_preview", fake_finalize)
    return captured


@pytest.mark.asyncio
async def test_a_client_caption_posts_verbatim(monkeypatch, drafted) -> None:
    """The sheet text becomes the caption UNTOUCHED, and hashtags are cleared so
    the publish-time join cannot append anything to the client's words."""

    async def sheet_says(title):
        return "Karen's exact words. As typed."

    monkeypatch.setattr(scheduled.calendar_sheet, "exact_caption", sheet_says)
    entry = load_calendar()[0]
    await scheduled.draft_calendar_entry(entry, publish_today=True)

    generated = drafted["generated"]
    assert generated.caption == "Karen's exact words. As typed."
    assert generated.hashtags == []
    assert drafted["extra_render_meta"]["caption_locked"] is True
    assert "calendar sheet" in drafted["caption_prefix"]


@pytest.mark.asyncio
async def test_a_struck_phrase_in_the_client_caption_is_flagged_not_rewritten(
    monkeypatch, drafted
) -> None:
    async def sheet_says(title):
        return "Halal certified, shipped to 90+ countries"

    monkeypatch.setattr(scheduled.calendar_sheet, "exact_caption", sheet_says)
    entry = load_calendar()[0]
    await scheduled.draft_calendar_entry(entry, publish_today=True)

    assert drafted["generated"].caption == "Halal certified, shipped to 90+ countries"
    assert "⚠️" in drafted["caption_prefix"]


@pytest.mark.asyncio
async def test_an_empty_cell_leaves_the_ai_caption_alone(monkeypatch, drafted) -> None:
    async def sheet_empty(title):
        return None

    monkeypatch.setattr(scheduled.calendar_sheet, "exact_caption", sheet_empty)
    entry = load_calendar()[0]
    await scheduled.draft_calendar_entry(entry, publish_today=True)

    assert drafted["generated"].caption == "AI caption"
    assert drafted["generated"].hashtags == ["#Globex"]
    assert drafted["extra_render_meta"]["caption_locked"] is False
    assert "calendar sheet" not in drafted["caption_prefix"]


# --------------------------------------------------------------------------- #
# write-back after publishing
# --------------------------------------------------------------------------- #


@pytest.fixture()
def published(monkeypatch):
    """Run publish_post with Blotato and the DB faked; capture the write-back."""
    entry = load_calendar()[0]
    wrote: dict[str, str] = {}

    post_row = {
        "id": "p1",
        "caption": "Final caption",
        "hashtags": ["#Globex", "#Poultry"],
        "image_url": "https://cdn.test/p1.png",
        "target_platforms": None,
        "event_type": EVENT_TYPE,
        "event_id": entry.event_id,
    }

    async def fake_publish(media, caption, hashtags, targets):
        return {Platform.instagram: PublishResult(Platform.instagram, True, url="ig://1")}

    async def fake_write_back(title, caption):
        wrote["title"] = title
        wrote["caption"] = caption
        return True

    monkeypatch.setattr(publisher.posts, "get", lambda pid: dict(post_row))
    monkeypatch.setattr(publisher.posts, "set_status", lambda pid, s: {})
    monkeypatch.setattr(publisher.post_platforms, "record", lambda *a, **kw: {})
    monkeypatch.setattr(publisher.blotato, "publish", fake_publish)

    from app.publishing import calendar_sheet as sheet_mod

    monkeypatch.setattr(sheet_mod, "write_back", fake_write_back)
    return post_row, wrote, entry


@pytest.mark.asyncio
async def test_publishing_writes_the_as_posted_caption_into_the_sheet(published) -> None:
    _post, wrote, entry = published
    await publisher.publish_post("p1")
    assert wrote["title"] == entry.title
    assert wrote["caption"] == "Final caption\n\n#Globex #Poultry"  # the Instagram string


@pytest.mark.asyncio
async def test_on_demand_posts_never_touch_the_sheet(published, monkeypatch) -> None:
    post_row, wrote, _entry = published
    post_row["event_type"] = None
    post_row["event_id"] = None
    monkeypatch.setattr(publisher.posts, "get", lambda pid: dict(post_row))
    await publisher.publish_post("p1")
    assert wrote == {}


@pytest.mark.asyncio
async def test_a_sheet_failure_never_fails_the_publish(published, monkeypatch) -> None:
    from app.publishing import calendar_sheet as sheet_mod

    async def exploding(title, caption):
        raise RuntimeError("sheet down")

    monkeypatch.setattr(sheet_mod, "write_back", exploding)
    results = await publisher.publish_post("p1")
    assert results[Platform.instagram].success  # publish outcome untouched
