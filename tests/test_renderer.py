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

NAVY = (0, 45, 114)
CYAN = (91, 194, 231)

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
    assert "font-family: 'Montserrat'" in html or "Montserrat" in html
    assert "data:font/woff2" in html  # fonts inlined
    assert "data:image/png" in html  # logo inlined (appears on every post)
    assert 'class="brand-footer"' in html
    for value in slots.values():
        assert value in html  # required slot text actually rendered


def test_base_css_uses_only_brand_colors() -> None:
    css = (HTML_DIR / "_base.css").read_text(encoding="utf-8")
    allowed_hex = {"002d72", "5bc2e7", "ffffff", "fff"}
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
