"""No rendered post may carry words painted over words.

The four approved layouts position every text block absolutely, so a headline
that wraps lands on the line beneath it. The base template's fit guard shrinks
the colliding block and the renderer refuses to screenshot anything still
overlapping — this is the contract those two pieces keep together.
"""

from __future__ import annotations

import asyncio

import pytest

from app.templates import renderer as render_mod
from app.templates.catalog import PLATFORM_DIMENSIONS, TEMPLATES

# Copy that broke the first client review batch (2026-09-11): 8 words on the
# editorial masthead wrapped to two lines and painted over the meta line.
LONG_HEADLINE = "TO THE PEOPLE WHO KEEP THE WORLD FED"
META = ["Plant Floors", "Ports", "Trucks", "Trading Desks"]
PHOTO = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


async def _overlaps_after_fit(variant: str, slots: dict) -> list[str]:
    """Render the variant and return whatever the fit guard could not resolve."""
    r = render_mod.Renderer()
    await r.start()
    try:
        html = r.render_html(variant, slots)
        width, height = PLATFORM_DIMENSIONS[TEMPLATES[variant].canvas]
        page = await r._browser.new_page(viewport={"width": width, "height": height})
        try:
            await page.set_content(html, wait_until="networkidle")
            await page.evaluate("() => document.fonts.ready")
            return list(await page.evaluate("() => window.__fitText()"))
        finally:
            await page.close()
    finally:
        await r.stop()


def _slots_for(variant: str) -> dict:
    slots = {"headline": LONG_HEADLINE, "photo": PHOTO}
    if variant == "ts_p3_editorial":
        slots["meta"] = META
    elif variant == "ms_3_anniv_photo":
        slots.update(
            name=LONG_HEADLINE,
            message="Founded 1993 | Shipped Globally",
            years="33",
            eyebrow="FOUNDING DAY",
            role="GLOBEX INTERNATIONAL",
        )
    else:
        slots.update(subline_strong="Plant Floors | Ports", subline_soft="Trucks | Trading Desks")
    return slots


@pytest.mark.parametrize("variant", ["ts_p3_editorial", "ts_p1_bolddip", "ts_p2_cut_navyborder"])
def test_a_wrapping_headline_is_fitted_not_painted_over_the_line_below(variant: str) -> None:
    assert asyncio.run(_overlaps_after_fit(variant, _slots_for(variant))) == []


def test_a_headline_that_fits_is_left_exactly_as_designed() -> None:
    """The guard must be a no-op for normal copy, or the reference-matching
    geometry tests would drift."""

    async def run() -> tuple[float, list[str]]:
        r = render_mod.Renderer()
        await r.start()
        try:
            slots = {**_slots_for("ts_p3_editorial"), "headline": "HAPPY EASTER"}
            html = r.render_html("ts_p3_editorial", slots)
            page = await r._browser.new_page(viewport={"width": 1080, "height": 1350})
            try:
                await page.set_content(html, wait_until="networkidle")
                await page.evaluate("() => document.fonts.ready")
                before = await page.evaluate(
                    "() => getComputedStyle(document.querySelector('h1')).fontSize"
                )
                left = await page.evaluate("() => window.__fitText()")
                after = await page.evaluate(
                    "() => getComputedStyle(document.querySelector('h1')).fontSize"
                )
                assert before == after, f"guard touched a headline that fit: {before} -> {after}"
                return float(after[:-2]), list(left)
            finally:
                await page.close()
        finally:
            await r.stop()

    size, left = asyncio.run(run())
    assert left == [] and abs(size - 60.25) < 0.01


def test_the_renderer_refuses_to_ship_an_overlap(monkeypatch) -> None:
    """If the guard ever reports a collision it could not clear, the render
    fails loudly instead of returning a broken picture."""

    async def run() -> None:
        r = render_mod.Renderer()
        await r.start()
        try:
            # Force the guard to report a collision regardless of the copy.
            real = r.render_html

            def poisoned(variant, slots):
                return real(variant, slots).replace(
                    "window.__fitText = function () {",
                    "window.__fitText = function () { return ['h1.txt x p.txt.meta']; };"
                    "window.__unused = function () {",
                )

            monkeypatch.setattr(r, "render_html", poisoned)
            with pytest.raises(render_mod.TextOverlapError):
                await r.render("ts_p3_editorial", _slots_for("ts_p3_editorial"))
        finally:
            await r.stop()

    asyncio.run(run())
