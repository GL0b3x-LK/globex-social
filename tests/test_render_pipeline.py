"""render_pipeline tests: a supplied photo must always land on a photo template,
and the eyebrow on the image must be the one the post carries."""

from __future__ import annotations

import pytest

from app.ai.generator import GeneratedPost
from app.workflows import render_pipeline


def _stats_post(**overrides) -> GeneratedPost:
    """A fresh post per test — ``render_and_store`` stamps the resolved eyebrow
    back onto it, so a module-level instance would leak between tests."""
    fields = dict(
        caption="150 ships on the water.",
        hashtags=["#GlobexInternational"],
        template_variant="stats",
        headline="On the water right now",
        figure="150",
        figure_unit="Ships",
        rationale="number-led",
    )
    fields.update(overrides)
    return GeneratedPost(**fields)


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    async def fake_render(variant, slots, **kwargs):
        seen["variant"] = variant
        seen["slots"] = slots
        return b"\x89PNG\r\n\x1a\n"

    async def fake_upload(post_id, png, *, suffix=""):
        seen["suffix"] = suffix
        return f"https://img.test/{post_id}{suffix}.png"

    monkeypatch.setattr("app.workflows.render_pipeline.render_mod.renderer.render", fake_render)
    monkeypatch.setattr("app.workflows.render_pipeline.storage.upload_png", fake_upload)
    return seen


async def test_attached_photo_forces_photo_template(captured) -> None:
    url = await render_pipeline.render_and_store(
        "p1", _stats_post(), photo_bytes=b"jpeg-bytes", photo_media_type="image/jpeg"
    )
    assert url == "https://img.test/p1.png"
    assert captured["variant"] == "custom"  # stats overridden because a photo is present
    assert "photo" in captured["slots"]


async def test_no_photo_keeps_model_variant(captured) -> None:
    await render_pipeline.render_and_store("p2", _stats_post())
    assert captured["variant"] == "stats"
    assert "photo" not in captured["slots"]


async def test_a_post_without_a_label_gets_the_templates_standard_one(captured) -> None:
    post = _stats_post()
    await render_pipeline.render_and_store("p3", post)
    assert captured["slots"]["eyebrow"] == "Globex by the numbers"
    # Stamped back, so the stored draft says what the picture says and the next
    # edit has something to change.
    assert post.eyebrow == "Globex by the numbers"


async def test_the_posts_own_label_beats_the_templates(captured) -> None:
    post = _stats_post(eyebrow="COMPANY ANNIVERSARY")
    await render_pipeline.render_and_store("p4", post)
    assert captured["slots"]["eyebrow"] == "COMPANY ANNIVERSARY"


async def test_an_empty_label_removes_it_from_the_image(captured) -> None:
    """ "Drop the label" has to be expressible — and must not read as "unset"."""
    post = _stats_post(eyebrow="")
    await render_pipeline.render_and_store("p5", post)
    assert captured["slots"]["eyebrow"] == ""


async def test_a_scheduler_supplied_label_beats_the_template_but_not_the_post(
    captured,
) -> None:
    await render_pipeline.render_and_store("p6", _stats_post(), context={"eyebrow": "From the log"})
    assert captured["slots"]["eyebrow"] == "From the log"

    await render_pipeline.render_and_store(
        "p7", _stats_post(eyebrow="Operator's words"), context={"eyebrow": "From the log"}
    )
    assert captured["slots"]["eyebrow"] == "Operator's words"


async def test_a_template_with_no_label_slot_carries_no_label(captured) -> None:
    """TS-p1 has no eyebrow. A stored value the image never shows is a lie the
    next edit would read as truth."""
    post = _stats_post(template_variant="ts_p1_bolddip", eyebrow="MEET US AT")
    await render_pipeline.render_and_store(
        "p8", post, photo_bytes=b"jpeg", photo_media_type="image/jpeg"
    )
    assert captured["variant"] == "ts_p1_bolddip"
    assert captured["slots"]["eyebrow"] == ""
    assert post.eyebrow == ""
