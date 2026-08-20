"""render_pipeline tests: a supplied photo must always land on a photo template,
and the eyebrow on the image must be the one the post carries."""

from __future__ import annotations

import pytest

from app.ai.generator import GeneratedPost
from app.templates import catalog
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

    def fake_upload(path, data, content_type, **kw):
        seen["path"] = path
        seen["content_type"] = content_type
        return f"https://img.test/{path}"

    monkeypatch.setattr("app.workflows.render_pipeline.render_mod.renderer.render", fake_render)
    monkeypatch.setattr("app.workflows.render_pipeline.storage.upload_bytes", fake_upload)
    return seen


async def test_attached_photo_forces_an_approved_photo_template(captured) -> None:
    """The switch used to land on `custom`, a demo-era template that renders from
    _base.css in Montserrat — which is how a from-scratch post came back in the
    wrong typeface."""
    url = await render_pipeline.render_and_store(
        "p1", _stats_post(), photo_bytes=b"jpeg-bytes", photo_media_type="image/jpeg"
    )
    assert url == "https://img.test/p1.png"
    assert captured["variant"] == catalog.DEFAULT_FINAL
    assert catalog.is_final(captured["variant"])
    assert "photo" in captured["slots"]


async def test_no_photo_keeps_model_variant(captured) -> None:
    await render_pipeline.render_and_store("p2", _stats_post())
    assert captured["variant"] == "stats"
    assert "photo" not in captured["slots"]


# --------------------------------------------------------------------------- #
# the approved four
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("alias", sorted(catalog.CALENDAR_TEMPLATE_ALIASES))
def test_the_names_the_client_uses_resolve_to_an_approved_template(alias: str) -> None:
    """Karen and Mike say "TS-p3-editorial_4x5", not "ts_p3_editorial". Both the
    free-form prompt and a typed request use the client's spelling."""
    resolved = render_pipeline.resolve_variant(alias)
    assert catalog.is_final(resolved)


def test_an_unrecognised_variant_falls_back_to_an_approved_template() -> None:
    """It used to fall back to `promotional` — off the approved set and in the
    wrong typeface, with only a log line to say so."""
    assert render_pipeline.resolve_variant("something_invented") == catalog.DEFAULT_FINAL
    assert catalog.is_final(render_pipeline.resolve_variant("something_invented"))


async def test_a_photo_template_with_no_photo_takes_one_from_the_bank(
    captured, monkeypatch
) -> None:
    """All four approved templates are built around a photograph, so a
    from-scratch post landing on one with no image would render an empty frame.
    Falling back to a non-approved template is exactly what was asked to stop."""
    from app.workflows import scheduled

    monkeypatch.setattr(
        scheduled, "pick_photo_for_text", lambda *a, **kw: scheduled._POOL_DIR / "brand-box.jpg"
    )
    monkeypatch.setattr(scheduled, "recently_used", lambda *a, **kw: frozenset())

    await render_pipeline.render_and_store(
        "p9", _stats_post(template_variant="ts_p2_cut_navyborder")
    )
    assert captured["variant"] == "ts_p2_cut_navyborder"
    assert captured["slots"]["photo"].startswith("data:image/jpeg;base64,")


def test_the_free_form_prompt_only_offers_approved_templates() -> None:
    """The prompt is the whole reason a from-scratch post could not reach an
    approved template: the names were not on the menu. Guard against a demo-era
    variant creeping back into it."""
    from app.ai.prompts.freeform import FREEFORM_PROMPT

    for alias in catalog.CALENDAR_TEMPLATE_ALIASES:
        assert alias in FREEFORM_PROMPT
    demo_only = set(catalog.TEMPLATES) - set(catalog.FINAL_VARIANTS)
    assert not [v for v in demo_only if f'"{v}"' in FREEFORM_PROMPT]


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


# --------------------------------------------------------------------------- #
# a named template binds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("use template TS-p2-cut-navyborder_4x5 for this", "ts_p2_cut_navyborder"),
        ("put it on ts-p2 please", "ts_p2_cut_navyborder"),
        ("use p2", "ts_p2_cut_navyborder"),
        ("the navy border one", "ts_p2_cut_navyborder"),
        ("TS-p1-bolddip_4x5 with the booth pill", "ts_p1_bolddip"),
        ("make it the editorial layout", "ts_p3_editorial"),
        ("MS-3-anniv-photo_4x5 for Jane", "ms_3_anniv_photo"),
    ],
)
def test_a_named_template_is_recognised(text: str, expected: str) -> None:
    assert catalog.named_template(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "post about our duck retail pack",  # names nothing
        "make it more like p2 than p1",  # names two — a comparison, not a choice
        "",
    ],
)
def test_no_single_named_template_means_no_pin(text: str) -> None:
    assert catalog.named_template(text) is None


async def test_a_named_template_overrides_the_models_choice(monkeypatch) -> None:
    """Asked in so many words for TS-p2-cut-navyborder_4x5, the model emitted
    TS-p1 — and every complaint that followed (wrong font, logo far right, no
    divider, the dip curvature) was TS-p1 faithfully rendering a request that
    said TS-p2. The name binds in code now; the model gets no vote."""
    from app.ai import generator

    async def model_prefers_the_workhorse(**kwargs):
        return GeneratedPost(
            caption="c",
            hashtags=["#Globex"],
            template_variant="TS-p1-bolddip_4x5",
            headline="The Full Cut Sheet",
            rationale="r",
        )

    async def no_learned_rules() -> str:
        return ""

    from app.ai import learning

    monkeypatch.setattr(generator, "generate_structured", model_prefers_the_workhorse)
    monkeypatch.setattr(learning, "rules_block_async", no_learned_rules)

    post = await generator.generate_freeform(
        "a cut sheet post — use template TS-p2-cut-navyborder_4x5"
    )
    assert post.template_variant == "ts_p2_cut_navyborder"


# --------------------------------------------------------------------------- #
# WhatsApp's 5MB media ceiling
# --------------------------------------------------------------------------- #


def _png_of(width: int, height: int, *, noise: bool) -> bytes:
    """A PNG that compresses well (flat) or badly (noise)."""
    from io import BytesIO

    from PIL import Image

    if noise:
        import os

        img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        img = Image.new("RGB", (width, height), (0, 45, 112))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_a_normal_render_is_sent_untouched() -> None:
    """Most posts are 1-3MB and must not be re-encoded — the PNG is what the
    approved templates were calibrated against."""
    png = _png_of(400, 500, noise=False)
    data, ext, content_type = render_pipeline.within_delivery_limit(png)
    assert data is png
    assert (ext, content_type) == ("png", "image/png")


def test_an_oversized_render_is_re_encoded_under_the_limit() -> None:
    """The failure this closes: a 5.12MB trade-show photo that Twilio accepted
    and WhatsApp then rejected with 63021, taking the day's post with it."""
    png = _png_of(2160, 2700, noise=True)
    assert len(png) > render_pipeline.WHATSAPP_MEDIA_LIMIT, "fixture must exceed the limit"

    data, ext, content_type = render_pipeline.within_delivery_limit(png)
    assert len(data) < render_pipeline.WHATSAPP_MEDIA_LIMIT
    assert (ext, content_type) == ("jpg", "image/jpeg")


def test_the_re_encoded_image_keeps_its_dimensions() -> None:
    """Quality may drop; the 4:5 canvas may not — the templates are pixel-calibrated."""
    from io import BytesIO

    from PIL import Image

    png = _png_of(2160, 2700, noise=True)
    data, _, _ = render_pipeline.within_delivery_limit(png)
    assert Image.open(BytesIO(data)).size == (2160, 2700)
