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
# Typography measured against the approved references in ~/Downloads/FINAL
# --------------------------------------------------------------------------- #

# Ink bounding boxes of each text row, measured 2026-08-12 on the 2160x2700
# reference PNGs (2x the 1080x1350 logical page): (x0, width, y0, cap-height).
# These pin the DESIGN, not the font name — a right typeface at the wrong size
# or position is still not the approved layout.
_REF_ROWS: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "ts_p1_bolddip": ((130, 887, 2353, 36), (129, 903, 2431, 49)),
    "ts_p2_cut_navyborder": ((126, 1174, 2304, 100), (121, 859, 2439, 52)),
    "ts_p3_editorial": ((89, 1166, 129, 93), (81, 1476, 268, 50)),
}
_REF_REGION = {
    "ts_p1_bolddip": (2300, 2520),
    "ts_p2_cut_navyborder": (2260, 2520),
    "ts_p3_editorial": (100, 360),
}
_REF_SLOTS = {
    "ts_p1_bolddip": {
        "photo": "",
        "headline": "Food & Hospitality Asia",
        "subline_strong": "21\u201324 April \u00b7 Singapore, Singapore",
        "subline_soft": "",
        "pill": "Booth 7C4-01",
    },
    "ts_p2_cut_navyborder": {
        "photo": "",
        "headline": "USAPEEC Americas Expo",
        "subline_strong": "18\u201320 March \u00b7 ",
        "subline_soft": "Bogot\u00e1, Colombia",
    },
    "ts_p3_editorial": {
        "photo": "",
        "headline": "Food & Hotel Vietnam",
        "meta": ["24\u201426 March, 2026 \u2022 Ho Chi Minh City, Vietnam \u2022 Stand A108"],
    },
}


def _ink_rows(png: bytes, y0: int, y1: int, x1: int = 2100):
    import numpy as np

    ink = np.asarray(Image.open(io.BytesIO(png)).convert("L"))[y0:y1, :x1] > 140
    rows = ink.any(axis=1)
    out, start = [], None
    for i, on in enumerate(rows):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= 6:
                xs = np.where(ink[start:i].any(axis=0))[0]
                out.append((int(xs.min()), int(xs.max() - xs.min() + 1), y0 + start, i - start))
            start = None
    return out


@pytest.mark.parametrize("variant", sorted(_REF_ROWS))
def test_text_lands_where_the_approved_reference_puts_it(render_sync, variant: str) -> None:
    y0, y1 = _REF_REGION[variant]
    got = _ink_rows(render_sync(variant, _REF_SLOTS[variant], dimensions=(1080, 1350)), y0, y1)
    want = _REF_ROWS[variant]
    assert len(got) >= len(want), f"{variant}: found {len(got)} text rows, expected {len(want)}"
    for i, (x0, width, top, cap) in enumerate(want):
        gx0, gw, gtop, gcap = got[i]
        assert abs(gx0 - x0) <= 3, f"{variant} row {i}: left edge {gx0} vs {x0}"
        assert abs(gw - width) <= 6, f"{variant} row {i}: width {gw} vs {width}"
        assert abs(gtop - top) <= 3, f"{variant} row {i}: top {gtop} vs {top}"
        assert abs(gcap - cap) <= 3, f"{variant} row {i}: cap height {gcap} vs {cap}"
