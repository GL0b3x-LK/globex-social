"""Template rendering tests.

Two tiers:
  * Always-on unit tests (no browser): HTML composition + a static brand-palette
    lint of _base.css. These run anywhere.
  * Browser tests (gated by the ``render_sync`` fixture, which skips if Playwright
    Chromium can't launch): every template renders to a valid, correctly-sized PNG;
    a per-template brand-palette audit; and a render-performance check that proves
    the browser-reuse pattern.

Brand audit rationale: the only inks are navy (#002D72), cyan (#5BC2E7), and white,
so a correct render has (a) ~zero saturated non-blue pixels and (b) every non-white
pixel sitting in the navy–cyan–white system. Dominance is theme-aware: navy posts
are ink-dominated; light posts are a white field carrying brand inks.
"""

from __future__ import annotations

import asyncio
import colorsys
import io
import re
import time

import pytest
from PIL import Image

from app.templates.catalog import TEMPLATES
from app.templates.renderer import HTML_DIR, Renderer

# Website brand values (client-confirmed June/July 2026) — NOT the old Pantone
# conversions (0,45,114)/(91,194,231).
NAVY = (0, 45, 112)
CYAN = (91, 192, 222)

# Renderer for HTML-only unit tests (constructing it launches no browser).
_R = Renderer()


def _min_slots(variant: str) -> dict[str, str]:
    """Fill just the required slots with placeholder text for a given template."""
    out: dict[str, str] = {}
    for name in TEMPLATES[variant].required_slots:
        out[name] = {"figure": "33", "name": "Maria Alvarez"}.get(name, f"Sample {name}")
    return out


# --------------------------------------------------------------------------- #
# Always-on unit tests (no browser)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variant", list(TEMPLATES))
def test_catalog_template_file_exists(variant: str) -> None:
    assert (HTML_DIR / TEMPLATES[variant].file).exists()


@pytest.mark.parametrize("variant", list(TEMPLATES))
def test_render_html_composes(variant: str) -> None:
    slots = _min_slots(variant)
    html = _R.render_html(variant, slots)
    assert "data:font/woff2" in html  # fonts inlined
    assert "data:image/png" in html  # logo inlined (appears on every post)
    if TEMPLATES[variant].file.startswith(("ts_", "ms_")):
        # Final approved set: standalone layout (no legacy brand-footer), and the
        # logo asset must still be present on every post.
        assert "lockup" in html or "logo" in html
        assert "#002D70" in html or "--navy: #002D70" in html
    else:
        assert "font-family: 'Montserrat'" in html or "Montserrat" in html
        assert 'class="brand-footer"' in html
    for value in slots.values():
        assert value in html  # required slot text actually rendered


def test_base_css_uses_only_brand_colors() -> None:
    css = (HTML_DIR / "_base.css").read_text(encoding="utf-8")
    allowed_hex = {"002d70", "5bc0de", "ffffff", "fff"}
    for hex_code in re.findall(r"#([0-9a-fA-F]{3,6})", css):
        assert hex_code.lower() in allowed_hex, f"off-brand hex #{hex_code} in _base.css"
    allowed_rgb = {NAVY, CYAN, (255, 255, 255)}
    for trip in re.findall(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", css):
        assert tuple(map(int, trip)) in allowed_rgb, f"off-brand rgb{trip} in _base.css"


# --------------------------------------------------------------------------- #
# Browser-backed fixture (manages its own event loop; reuses one browser)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def render_sync():
    loop = asyncio.new_event_loop()
    renderer = Renderer()
    try:
        loop.run_until_complete(renderer.start())
    except Exception as exc:  # noqa: BLE001 — no Chromium in this env -> skip, don't fail
        loop.close()
        pytest.skip(f"Playwright Chromium unavailable: {exc}")

    def _render(variant: str, slots: dict | None = None, **kwargs) -> bytes:
        return loop.run_until_complete(
            renderer.render(variant, slots if slots is not None else _min_slots(variant), **kwargs)
        )

    yield _render
    loop.run_until_complete(renderer.stop())
    loop.close()


def _palette_stats(im: Image.Image) -> dict[str, float]:
    im = im.convert("RGB").resize((220, 220))
    pixels = list(im.getdata())
    total = len(pixels)
    white = ink = off = blue_nw = nonwhite = 0
    for r, g, b in pixels:
        is_white = min(r, g, b) >= 235
        if is_white:
            white += 1
        else:
            nonwhite += 1
        h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        hue = h * 360
        in_blue_band = 175 <= hue <= 250
        if _dist((r, g, b), NAVY) <= 60 or _dist((r, g, b), CYAN) <= 60:
            ink += 1
        if s >= 0.25 and not in_blue_band:
            off += 1
        if not is_white and (in_blue_band or s < 0.12):
            blue_nw += 1
    nw = max(nonwhite, 1)
    return {"white": white / total, "ink": ink / total, "off": off / total, "blue_nw": blue_nw / nw}


def _dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5


# --------------------------------------------------------------------------- #
# Browser tests (gated)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variant", list(TEMPLATES))
def test_template_renders_and_is_on_brand(render_sync, variant: str) -> None:
    png = render_sync(variant)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    im = Image.open(io.BytesIO(png))
    assert im.size == (2160, 2160), f"unexpected size {im.size}"  # 1080 logical @ 2x

    stats = _palette_stats(im)
    assert stats["off"] < 0.01, f"{variant}: off-brand hue fraction {stats['off']:.4f}"
    assert stats["blue_nw"] > 0.95, f"{variant}: non-white blue-family only {stats['blue_nw']:.2f}"
    if TEMPLATES[variant].theme in ("navy", "cyan"):
        assert stats["ink"] > 0.40, f"{variant}: navy/cyan ink {stats['ink']:.2f} < 0.40"
    else:  # light: a white field carrying brand inks
        assert stats["white"] > 0.50, f"{variant}: white field only {stats['white']:.2f}"
        assert stats["ink"] > 0.005, f"{variant}: no brand ink present ({stats['ink']:.3f})"


def test_render_performance(render_sync) -> None:
    """Median warm render < 1500ms — proves browser reuse (a per-render launch is ~1-2s each)."""
    non_photo = [v for v, spec in TEMPLATES.items() if not spec.needs_photo]
    render_sync(non_photo[0])  # warm up (first page/font paint)
    times = []
    for variant in non_photo:
        start = time.perf_counter()
        render_sync(variant)
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    median = times[len(times) // 2]
    assert median < 1500, f"median warm render {median:.0f}ms; all={[round(t) for t in times]}"


# --------------------------------------------------------------------------- #
# TS-p1 typeface: matched to Mike's approved reference, not guessed from it
# --------------------------------------------------------------------------- #

# ~/Downloads/FINAL/TS-p1-bolddip_4x5.png, measured 2026-08-12 (2160x2700 canvas,
# i.e. 2x the 1080x1350 logical page). These are the ink bounding boxes of the
# headline and subline rows in the navy panel.
_REF_P1_HEADLINE = {"x0": 130, "width": 887, "y0": 2353, "height": 36}
_REF_P1_SUBLINE = {"x0": 129, "width": 903, "y0": 2431}


def test_ts_p1_uses_the_reference_typeface_not_a_lookalike() -> None:
    """TS-p1 was built with Comfortaa on a guess and shipped that way; the
    client's designer spotted it in the rendered posts. The reference 'a' is
    double-storey and its cap 'D' aspect is 1.03 — both Montserrat, neither
    Comfortaa nor Poppins (single-storey 'a', D aspect 0.79/0.90)."""
    css = (HTML_DIR / "ts_p1_bolddip.html").read_text(encoding="utf-8")
    families = re.findall(r"font-family:\s*'([^']+)'", css)
    # headline, subline and pill — all three draw from the reference family.
    assert families == ["Montserrat"] * 3, f"TS-p1 draws from {set(families)}"


@pytest.mark.parametrize("row", ["headline", "subline"])
def test_ts_p1_text_lands_where_the_approved_reference_puts_it(render_sync, row: str) -> None:
    """Swapping the family changes cap height and ascent, so the sizes and the
    box positions have to be re-solved with it — a right font in the wrong place
    is still not the approved design."""
    import numpy as np

    png = render_sync(
        "ts_p1_bolddip",
        {
            "photo": "",
            "headline": "Food & Hospitality Asia",
            "subline_strong": "21–24 April · Singapore, Singapore",
            "subline_soft": "",
            "pill": "Booth 7C4-01",
        },
        dimensions=(1080, 1350),
    )
    img = Image.open(io.BytesIO(png)).convert("L")
    ink = np.asarray(img)[2300:2520, :2100] > 140

    rows = ink.any(axis=1)
    bands: list[tuple[int, int]] = []
    start = None
    for i, on in enumerate(rows):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start > 6:
                bands.append((start, i))
            start = None
    assert len(bands) >= 2, "expected a headline row and a subline row in the panel"

    idx = 0 if row == "headline" else 1
    y0, y1 = bands[idx]
    band = ink[y0:y1]
    xs = np.where(band.any(axis=0))[0]
    expected = _REF_P1_HEADLINE if row == "headline" else _REF_P1_SUBLINE

    assert abs(int(xs.min()) - expected["x0"]) <= 3, f"{row} left edge drifted"
    assert abs(int(xs.max() - xs.min() + 1) - expected["width"]) <= 6, f"{row} width drifted"
    assert abs((2300 + y0) - expected["y0"]) <= 3, f"{row} baseline drifted"
    if "height" in expected:
        assert abs((y1 - y0) - expected["height"]) <= 3, f"{row} cap height drifted"
