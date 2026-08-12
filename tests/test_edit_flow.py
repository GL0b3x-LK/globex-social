"""The edit loop, as the internal test run actually exercised it — and broke it.

Every test here is a fault Abdul hit on day one of the 2-hour cadence: a
dictated caption doubled its hashtags, an edit stripped the photo off a duck
post, the template changed itself, a scheduled post lost its calendar identity,
and a swipe-reply to a preview resolved to nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
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

    async def fake_classify(feedback):
        return "textual"

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
    monkeypatch.setattr(approval.editor, "classify_edit_kind", fake_classify)
    monkeypatch.setattr(approval.render_pipeline, "render_and_store", fake_render)
    monkeypatch.setattr(approval.image_gen, "download", fake_download)
    monkeypatch.setattr(approval.twilio_client, "send_media", fake_send_media)
    monkeypatch.setattr(approval.conversation, "transition", fake_transition)
    monkeypatch.setattr(approval.posts, "update", lambda pid, **kw: {})
    monkeypatch.setattr(approval.posts, "set_image_url", lambda pid, url: {})
    _wire_post_row(monkeypatch, cap, stored_meta)
    monkeypatch.setattr(approval.approvals, "record", lambda *a: None)
    return cap


def _wire_post_row(monkeypatch, cap: _Captures, stored_meta: dict[str, Any]) -> None:
    """Make posts.get/set_render_meta behave like the real table — reads see writes.

    A static ``get`` lies about anything that reads back what it just wrote (the
    delivery bookkeeping does exactly that), and the lie shows up as a passing
    test over broken behaviour, or the reverse.
    """
    row = {"id": "p1", "render_meta": stored_meta}

    def _set(pid: str, meta: dict[str, Any]) -> dict[str, Any]:
        row["render_meta"] = meta
        cap.saved_meta = meta
        return {}

    monkeypatch.setattr(approval.posts, "get", lambda pid: dict(row))
    monkeypatch.setattr(approval.posts, "set_render_meta", _set)


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


@pytest.mark.asyncio
async def test_picture_feedback_on_a_photo_post_goes_to_the_image_model(
    wired: _Captures, monkeypatch
) -> None:
    """'Images via nano-banana': a visual instruction transforms the stored photo
    (img2img) and re-renders — the copy is not regenerated at all."""
    from app.ai.image_gen import ImageResult

    async def classify_visual(feedback):
        return "visual"

    async def fake_img_edit(url, feedback, **kw):
        assert url == "https://cdn.test/p1-photo.jpg"
        return ImageResult(ok=True, image_bytes=b"new-png")

    async def fail_apply_edit(*a, **kw):  # the copy editor must NOT run
        raise AssertionError("visual edit must not rewrite the copy")

    async def fake_send_text(phone, body, **kw):
        return "SM0"

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.editor, "apply_edit", fail_apply_edit)
    monkeypatch.setattr(approval.image_gen, "edit", fake_img_edit)
    monkeypatch.setattr(approval.twilio_client, "send_text", fake_send_text)
    monkeypatch.setattr(
        approval.storage, "upload_bytes", lambda path, data, ctype: f"https://cdn.test/{path}"
    )

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "use a cold store photo instead")

    assert wired.render_kwargs.get("photo_bytes") == b"new-png"
    assert wired.saved_meta is not None
    assert wired.saved_meta["photo_url"].startswith("https://cdn.test/p1-photo-")
    assert wired.saved_meta["publish_on"] == "2026-08-11"  # identity still intact
    assert wired.sent and wired.sent[0]["post_id"] == "p1"


@pytest.mark.asyncio
async def test_a_failed_photo_edit_leaves_the_post_reviewable(
    wired: _Captures, monkeypatch
) -> None:
    from app.ai.image_gen import ImageResult

    async def classify_visual(feedback):
        return "visual"

    async def failing_img_edit(url, feedback, **kw):
        return ImageResult(ok=False, error="filter refused")

    texts: list[str] = []

    async def fake_send_text(phone, body, **kw):
        texts.append(body)
        return "SM0"

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.image_gen, "edit", failing_img_edit)
    monkeypatch.setattr(approval.twilio_client, "send_text", fake_send_text)

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "make the photo a sunset")

    assert wired.render_kwargs == {}  # nothing re-rendered
    assert len(texts) == 2  # "regenerating…" then the failure notice


# --------------------------------------------------------------------------- #
# a message that cannot be delivered must not cancel the work
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_failed_status_message_does_not_abandon_the_edit(
    wired: _Captures, monkeypatch
) -> None:
    """The real incident: WhatsApp hit its daily cap, the "Updating the image…"
    line raised, and the edit it was announcing never ran. That line goes out
    before the work starts, so its delivery must not be load-bearing."""
    from app.ai.image_gen import ImageResult

    seen: dict[str, str] = {}

    async def classify_visual(feedback):
        return "visual"

    async def fake_img_edit(url, feedback, **kw):
        seen["feedback"] = feedback
        return ImageResult(ok=True, image_bytes=b"new-png")

    async def dead_line(*_a, **_kw):
        raise RuntimeError("HTTP 429 error: exceeded the 50 daily messages limit")

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.image_gen, "edit", fake_img_edit)
    monkeypatch.setattr(approval.twilio_client, "send_text", dead_line)
    monkeypatch.setattr(approval.twilio_client, "send_media", dead_line)
    monkeypatch.setattr(
        approval.storage, "upload_bytes", lambda path, data, ctype: f"https://cdn.test/{path}"
    )

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "make the photo lighter")

    # The picture was regenerated and stored even though nothing could be sent —
    # so it is waiting to be re-sent, not lost.
    assert seen["feedback"] == "make the photo lighter"
    assert wired.render_kwargs.get("photo_bytes") == b"new-png"
    assert wired.saved_meta is not None
    assert wired.saved_meta["publish_on"] == "2026-08-11"  # identity still intact


# --------------------------------------------------------------------------- #
# day-two faults: attachments dropped, mixed edits halved, placeholders painted
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_attached_photo_replaces_the_picture_without_the_image_model(
    wired: _Captures, monkeypatch
) -> None:
    """The milestone preview says "reply with the employee's photo to swap it in".
    The reply used to be thrown away: the edit path took text only, so the photo
    never arrived and the gray placeholder stayed."""

    async def classify_visual(feedback: str) -> str:
        return "visual"

    async def must_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover - guard
        raise AssertionError("an attached photo must not be sent to the image model")

    async def fake_download(url: str, **kw: Any) -> tuple[bytes, str]:
        return b"mikes-photo", "image/jpeg"

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.image_gen, "edit", must_not_run)
    monkeypatch.setattr(approval.media, "download_twilio_media", fake_download)
    monkeypatch.setattr(
        approval.storage, "upload_bytes", lambda path, data, ctype: f"https://cdn.test/{path}"
    )

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request(
        "whatsapp:+1", convo, "add her image attached", photo=("https://api.twilio/x", "image/jpeg")
    )

    assert wired.render_kwargs.get("photo_bytes") == b"mikes-photo"
    assert wired.saved_meta is not None
    assert wired.saved_meta["photo_url"].startswith("https://cdn.test/p1-photo-")
    assert wired.saved_meta["publish_on"] == "2026-08-11"  # identity intact


@pytest.mark.asyncio
async def test_a_mixed_edit_changes_the_words_and_the_picture(
    wired: _Captures, monkeypatch
) -> None:
    """ "Change the subtitle to X. Also add her image attached." is one message
    asking for two things. Forcing it into a single bucket dropped whichever half
    lost — the dictated subtitle went to the image model as a scene prompt."""

    async def classify_both(feedback: str) -> str:
        return "both"

    async def fake_download(url: str, **kw: Any) -> tuple[bytes, str]:
        return b"mikes-photo", "image/jpeg"

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_both)
    monkeypatch.setattr(approval.media, "download_twilio_media", fake_download)
    monkeypatch.setattr(
        approval.storage, "upload_bytes", lambda path, data, ctype: f"https://cdn.test/{path}"
    )

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request(
        "whatsapp:+1",
        convo,
        "Change the subtitle to: Lana Petrenko | Accounting Manager. Also add her image attached.",
        photo=("https://api.twilio/x", "image/jpeg"),
    )

    # the picture changed...
    assert wired.render_kwargs.get("photo_bytes") == b"mikes-photo"
    # ...AND the copy did (the fixture's apply_edit returns EDITED CAPTION)
    assert wired.saved_meta is not None
    assert wired.saved_meta["generated"]["caption"] == "EDITED CAPTION"


@pytest.mark.asyncio
async def test_a_visual_edit_on_a_placeholder_asks_for_the_real_photo(
    wired: _Captures, monkeypatch
) -> None:
    """Sent to img2img, a gray placeholder comes back as an invented person —
    a fabricated face on a named employee's post, one 'approve' from publishing."""
    sent_texts: list[str] = []

    async def classify_visual(feedback: str) -> str:
        return "visual"

    async def must_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover - guard
        raise AssertionError("a placeholder must never reach the image model")

    async def fake_send_text(phone: str, body: str, **kw: Any) -> str:
        sent_texts.append(body)
        return "SM0"

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.image_gen, "edit", must_not_run)
    monkeypatch.setattr(approval.twilio_client, "send_text", fake_send_text)
    _wire_post_row(
        monkeypatch,
        wired,
        {
            "generated": _post().model_dump(),
            "treatment": "calendar",
            "publish_on": "2026-08-11",
            "photo_url": "https://cdn.test/p1-photo.jpg",
            "photo_media_type": "image/jpeg",
            "photo_is_placeholder": True,
        },
    )

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "make her look more professional")

    assert any("placeholder" in t for t in sent_texts)


# --------------------------------------------------------------------------- #
# the same three faults on the GENERATED-IMAGE branch, which kept the old
# binary visual/textual test long after the photo branch had been fixed
# --------------------------------------------------------------------------- #


@pytest.fixture()
def wired_generated(wired: _Captures, monkeypatch) -> _Captures:
    """`wired`, re-pointed at a post whose picture came from the image model."""
    _wire_post_row(
        monkeypatch,
        wired,
        {
            "generated": _post().model_dump(),
            "treatment": "generated_image",
            "raw_image_url": "https://cdn.test/p1-raw.png",
        },
    )
    monkeypatch.setattr(
        approval.storage, "upload_bytes", lambda path, data, ctype: f"https://cdn.test/{path}"
    )
    return wired


def _generated_convo() -> dict[str, Any]:
    return {
        "current_post_id": "p1",
        "context": {
            "generated": _post().model_dump(),
            "treatment": "generated_image",
            "raw_image_url": "https://cdn.test/p1-raw.png",
        },
    }


@pytest.mark.asyncio
async def test_a_mixed_edit_on_a_generated_image_changes_both(
    wired_generated: _Captures, monkeypatch
) -> None:
    """One message asking for a brighter picture AND new words must get both.
    This branch tested `kind == "visual"` alone, so "both" fell through to the
    copy-only path: the words changed, the picture silently did not."""

    async def classify_both(feedback: str) -> str:
        return "both"

    async def fake_image_edit(url: str, prompt: str, **kw: Any) -> Any:
        return SimpleNamespace(ok=True, image_bytes=b"brighter-png")

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_both)
    monkeypatch.setattr(approval.image_gen, "edit", fake_image_edit)

    await approval.handle_edit_request(
        "whatsapp:+1", _generated_convo(), "make it brighter and change the headline to READY NOW"
    )

    assert wired_generated.render_kwargs.get("photo_bytes") == b"brighter-png"
    assert wired_generated.saved_meta is not None
    assert wired_generated.saved_meta["generated"]["caption"] == "EDITED CAPTION"


@pytest.mark.asyncio
async def test_an_attached_photo_replaces_a_generated_image(
    wired_generated: _Captures, monkeypatch
) -> None:
    """A replacement photo was downloaded only AFTER the treatment branch, so on
    a generated-image post it was dropped without a word."""

    async def classify_visual(feedback: str) -> str:
        return "visual"

    async def must_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover - guard
        raise AssertionError("an attached photo must not be sent to the image model")

    async def fake_download(url: str, **kw: Any) -> tuple[bytes, str]:
        return b"mikes-photo", "image/jpeg"

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.image_gen, "edit", must_not_run)
    monkeypatch.setattr(approval.media, "download_twilio_media", fake_download)

    await approval.handle_edit_request(
        "whatsapp:+1",
        _generated_convo(),
        "use this shot instead",
        photo=("https://api.twilio/x", "image/jpeg"),
    )

    assert wired_generated.render_kwargs.get("photo_bytes") == b"mikes-photo"
    assert wired_generated.render_kwargs.get("photo_media_type") == "image/jpeg"


@pytest.mark.asyncio
async def test_each_image_tweak_chains_off_the_previous_picture(
    wired_generated: _Captures, monkeypatch
) -> None:
    """img2img references the LAST picture, and every version gets its own URL.

    Overwriting `{post_id}-raw.png` in place put the reference behind Supabase's
    hour-long CDN cache, so a second tweak could transform the picture the first
    one had already replaced.
    """
    referenced: list[str] = []

    async def classify_visual(feedback: str) -> str:
        return "visual"

    async def fake_image_edit(url: str, prompt: str, **kw: Any) -> Any:
        referenced.append(url)
        return SimpleNamespace(ok=True, image_bytes=b"round-2-png")

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_visual)
    monkeypatch.setattr(approval.image_gen, "edit", fake_image_edit)

    await approval.handle_edit_request("whatsapp:+1", _generated_convo(), "warmer light")

    assert referenced == ["https://cdn.test/p1-raw.png"]  # the picture it was shown
    assert wired_generated.saved_meta is not None
    new_url = wired_generated.saved_meta["raw_image_url"]
    assert new_url != "https://cdn.test/p1-raw.png", "a new version needs a new URL"

    # Round two reads the URL round one stored, so the chain continues.
    convo = _generated_convo()
    convo["context"]["raw_image_url"] = new_url
    await approval.handle_edit_request("whatsapp:+1", convo, "warmer still")
    assert referenced[-1] == new_url


@pytest.mark.asyncio
async def test_a_copy_edit_on_a_generated_image_leaves_the_picture_alone(
    wired_generated: _Captures, monkeypatch
) -> None:
    """Changing only words must not call the image model — a picture that drifts
    on a copy edit is a picture nobody asked to change."""

    async def classify_textual(feedback: str) -> str:
        return "textual"

    async def must_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover - guard
        raise AssertionError("a copy-only edit must not reach the image model")

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_textual)
    monkeypatch.setattr(approval.image_gen, "edit", must_not_run)

    await approval.handle_edit_request("whatsapp:+1", _generated_convo(), "shorten the caption")

    assert wired_generated.render_kwargs.get("photo_bytes") == b"jpeg-bytes"  # refetched, unchanged
    assert wired_generated.saved_meta is not None
    assert wired_generated.saved_meta["raw_image_url"] == "https://cdn.test/p1-raw.png"


@pytest.mark.asyncio
async def test_a_failed_generated_image_edit_still_applies_the_words(
    wired_generated: _Captures, monkeypatch
) -> None:
    """The image model falling over must not also cost the operator their copy
    change — the half that CAN be done still gets done."""

    async def classify_both(feedback: str) -> str:
        return "both"

    async def failing_edit(url: str, prompt: str, **kw: Any) -> Any:
        return SimpleNamespace(ok=False, image_bytes=None)

    monkeypatch.setattr(approval.editor, "classify_edit_kind", classify_both)
    monkeypatch.setattr(approval.image_gen, "edit", failing_edit)

    await approval.handle_edit_request(
        "whatsapp:+1", _generated_convo(), "brighter, and cut the last line"
    )

    assert wired_generated.saved_meta is not None
    assert wired_generated.saved_meta["generated"]["caption"] == "EDITED CAPTION"


@pytest.mark.asyncio
async def test_an_undelivered_preview_is_remembered_for_re_sending(
    wired: _Captures, monkeypatch
) -> None:
    """A send that fails is not the end of the story: the render exists, so the
    post is owed to that recipient until it actually arrives."""

    async def dead_send(*a: Any, **kw: Any) -> str:
        raise RuntimeError("HTTP 429 error: exceeded the 50 daily messages limit")

    monkeypatch.setattr(approval.twilio_client, "send_media", dead_send)

    convo = {
        "current_post_id": "p1",
        "context": {"generated": _post().model_dump(), "treatment": "calendar"},
    }
    await approval.handle_edit_request("whatsapp:+1", convo, "tighten the caption")

    assert wired.saved_meta is not None
    assert wired.saved_meta["undelivered"] == ["whatsapp:+1"]
    # the work still happened and is stored
    assert wired.saved_meta["generated"]["caption"] == "EDITED CAPTION"
