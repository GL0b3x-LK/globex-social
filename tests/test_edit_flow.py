"""The edit loop, as the internal test run actually exercised it — and broke it.

Every test here is a fault Abdul hit on day one of the 2-hour cadence: a
dictated caption doubled its hashtags, an edit stripped the photo off a duck
post, the template changed itself, a scheduled post lost its calendar identity,
and a swipe-reply to a preview resolved to nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai import editor
from app.ai.generator import GeneratedPost
from app.workflows import approval


def _post(**over: Any) -> GeneratedPost:
    base: dict[str, Any] = dict(
        caption="Premium duck, export-ready. Talk to us about volume.",
        hashtags=["#GlobexInternational", "#DuckExport"],
        template_variant="ts_p1_bolddip",
        headline="Premium Duck. Export-Ready.",
        subhead="Master cartons for buyers ordering at scale.",
        rationale="test",
    )
    base.update(over)
    return GeneratedPost(**base)


# --------------------------------------------------------------------------- #
# hashtags dictated inside a caption
# --------------------------------------------------------------------------- #


def test_a_hashtag_line_dictated_in_the_caption_moves_to_the_hashtags_field() -> None:
    """Caption and hashtags are joined at preview and publish, so tags left in
    the caption appear twice — exactly what the duck edit produced."""
    caption, tags = editor.split_hashtags(
        "PREMIUM DUCK EXPORT-READY\n\nIf duck is on your program, let's talk volume.\n\n"
        "#GlobexInternational #DuckExport #ColdChain",
        ["#GlobexInternational", "#FoodSupplyChain"],
    )
    assert "#" not in caption
    assert caption.endswith("let's talk volume.")
    assert tags == [
        "#GlobexInternational",
        "#FoodSupplyChain",
        "#DuckExport",
        "#ColdChain",
    ]


def test_duplicate_tags_differing_only_in_case_are_not_kept_twice() -> None:
    _caption, tags = editor.split_hashtags(
        "Line of text\n#globexinternational", ["#GlobexInternational"]
    )
    assert tags == ["#GlobexInternational"]


def test_a_tag_woven_into_a_sentence_stays_part_of_the_copy() -> None:
    """Only PURE hashtag lines move out — a tag mid-sentence is deliberate copy."""
    caption, tags = editor.split_hashtags("We ship #duck weekly to Asia", [])
    assert caption == "We ship #duck weekly to Asia"
    assert tags == []


# --------------------------------------------------------------------------- #
# unrequested drift
# --------------------------------------------------------------------------- #


def test_an_edit_that_never_mentioned_layout_cannot_change_the_template() -> None:
    """The duck caption edit came back with template product_spotlight — the
    calendar's approved template is not the model's to change."""
    current = _post()
    revised = _post(template_variant="product_spotlight", caption="New caption")
    editor.normalize(revised, current, "Change caption to: New caption")
    assert revised.template_variant == "ts_p1_bolddip"


def test_an_edit_that_asks_for_a_layout_change_may_change_the_template() -> None:
    current = _post()
    revised = _post(template_variant="stats")
    editor.normalize(revised, current, "use the stats layout for this one")
    assert revised.template_variant == "stats"


# --------------------------------------------------------------------------- #
# the edit re-render: photo kept, meta merged, message linked
# --------------------------------------------------------------------------- #


class _Captures:
    def __init__(self) -> None:
        self.render_kwargs: dict[str, Any] = {}
        self.saved_meta: dict[str, Any] | None = None
        self.sent: list[dict[str, Any]] = []


@pytest.fixture()
def wired(monkeypatch) -> _Captures:
    cap = _Captures()
    stored_meta = {
        "generated": _post().model_dump(),
        "treatment": "calendar",
        "publish_on": "2026-08-11",
        "calendar": {"week": 3, "title": "Premium Duck export carton"},
        "photo_url": "https://cdn.test/p1-photo.jpg",
        "photo_media_type": "image/jpeg",
    }

    async def fake_apply_edit(current, feedback, **_kw):
        return _post(caption="EDITED CAPTION")

    async def fake_render(post_id, post, **kw):
        cap.render_kwargs = kw
        return "https://cdn.test/p1.png"

    async def fake_download(url):
        return b"jpeg-bytes"

    async def fake_send_media(phone, body, media_url, **kw):
        cap.sent.append({"phone": phone, "media_url": media_url, **kw})
        return "SM1"

    async def fake_transition(*a, **kw):
        return {}

    monkeypatch.setattr(approval.editor, "apply_edit", fake_apply_edit)
    monkeypatch.setattr(approval.render_pipeline, "render_and_store", fake_render)
    monkeypatch.setattr(approval.image_gen, "download", fake_download)
    monkeypatch.setattr(approval.twilio_client, "send_media", fake_send_media)
    monkeypatch.setattr(approval.conversation, "transition", fake_transition)
    monkeypatch.setattr(approval.posts, "get", lambda pid: {"id": pid, "render_meta": stored_meta})
    monkeypatch.setattr(approval.posts, "update", lambda pid, **kw: {})
    monkeypatch.setattr(approval.posts, "set_image_url", lambda pid, url: {})
    monkeypatch.setattr(
        approval.posts,
        "set_render_meta",
        lambda pid, meta: cap.__setattr__("saved_meta", meta) or {},
    )
    monkeypatch.setattr(approval.approvals, "record", lambda *a: None)
    return cap


@pytest.mark.asyncio
async def test_an_edit_rerenders_with_the_original_photo(wired: _Captures) -> None:
    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "Change caption to: EDITED CAPTION")
    assert wired.render_kwargs.get("photo_bytes") == b"jpeg-bytes"
    assert wired.render_kwargs.get("photo_media_type") == "image/jpeg"


@pytest.mark.asyncio
async def test_an_edit_keeps_the_posts_calendar_identity(wired: _Captures) -> None:
    """publish_on and the calendar block survived the edit — losing them is what
    turned scheduled posts into on-demand ones."""
    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "tighten the caption")
    assert wired.saved_meta is not None
    assert wired.saved_meta["publish_on"] == "2026-08-11"
    assert wired.saved_meta["calendar"]["title"] == "Premium Duck export carton"
    assert wired.saved_meta["photo_url"] == "https://cdn.test/p1-photo.jpg"
    assert wired.saved_meta["generated"]["caption"] == "EDITED CAPTION"


@pytest.mark.asyncio
async def test_the_edit_preview_is_linked_to_its_post(wired: _Captures) -> None:
    """The outbound preview must carry post_id, or swipe-replying to it can
    never resolve back to this post."""
    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "tighten the caption")
    assert wired.sent and wired.sent[0]["post_id"] == "p1"


@pytest.mark.asyncio
async def test_a_lost_photo_degrades_to_a_render_not_a_crash(wired: _Captures, monkeypatch) -> None:
    async def failing_download(url):
        raise RuntimeError("410 gone")

    monkeypatch.setattr(approval.image_gen, "download", failing_download)
    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "tighten the caption")
    assert wired.render_kwargs.get("photo_bytes") is None  # rendered anyway
    assert wired.sent  # and the operator still got a preview
