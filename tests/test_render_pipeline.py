"""render_pipeline tests: a supplied photo must always land on a photo template."""

from __future__ import annotations

import pytest

from app.ai.generator import GeneratedPost
from app.workflows import render_pipeline

_STATS_POST = GeneratedPost(
    caption="150 ships on the water.",
    hashtags=["#GlobexInternational"],
    template_variant="stats",
    headline="On the water right now",
    figure="150",
    figure_unit="Ships",
    rationale="number-led",
)


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    async def fake_render(variant, slots, **kwargs):
        seen["variant"] = variant
        seen["slots"] = slots
        return b"\x89PNG\r\n\x1a\n"

    async def fake_upload(post_id, png):
        return f"https://img.test/{post_id}.png"

    monkeypatch.setattr("app.workflows.render_pipeline.render_mod.renderer.render", fake_render)
    monkeypatch.setattr("app.workflows.render_pipeline.storage.upload_png", fake_upload)
    return seen


async def test_attached_photo_forces_photo_template(captured) -> None:
    url = await render_pipeline.render_and_store(
        "p1", _STATS_POST, photo_bytes=b"jpeg-bytes", photo_media_type="image/jpeg"
    )
    assert url == "https://img.test/p1.png"
    assert captured["variant"] == "custom"  # stats overridden because a photo is present
    assert "photo" in captured["slots"]


async def test_no_photo_keeps_model_variant(captured) -> None:
    await render_pipeline.render_and_store("p2", _STATS_POST)
    assert captured["variant"] == "stats"
    assert "photo" not in captured["slots"]
