"""Render a brand template to a PNG via a reused headless Chromium.

The browser is launched ONCE (``start()``) and reused for every ``render()`` — per
the plan, browser reuse is ~5x faster than launch-per-render. FastAPI owns the
lifecycle in its lifespan; tests start/stop a module-local instance.

Assets (fonts, logos) are inlined as data URIs, so ``set_content`` needs no network
or file:// access and rendering is fully deterministic for pixel-baseline tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import Browser, Playwright, async_playwright

from app.templates.assets import all_logos, font_face_css
from app.templates.catalog import PLATFORM_DIMENSIONS, TEMPLATES

HTML_DIR = Path(__file__).parent / "html"
_BASE_CSS = (HTML_DIR / "_base.css").read_text(encoding="utf-8")

# Flags for deterministic, color-accurate, hermetic rendering.
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
    "--disable-lcd-text",  # grayscale AA -> stable across machines for pixel diffs
]


class Renderer:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._env = Environment(
            loader=FileSystemLoader(str(HTML_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self._env.globals.update(
            font_face_css=font_face_css(),
            base_css=_BASE_CSS,
            logos=all_logos(),
        )

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(args=_LAUNCH_ARGS)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    def render_html(self, template_variant: str, slots: dict[str, Any]) -> str:
        """Jinja-render the HTML for a variant (no browser needed). Useful in unit tests."""
        spec = TEMPLATES[template_variant]
        context = {"theme": spec.theme, **slots}
        return self._env.get_template(spec.file).render(**context)

    async def render_file(
        self,
        template_file: str,
        context: dict[str, Any],
        *,
        dimensions: tuple[int, int],
        transparent: bool = False,
        scale: float = 1.0,
    ) -> bytes:
        """Render an HTML file directly (not catalog-bound) to a PNG.

        Used for the VHS video overlay: a transparent 9:16 HUD composited onto
        Karen's video by ffmpeg. `transparent=True` keeps the page background clear
        (omit_background) so only the HUD elements paint.
        """
        if self._browser is None:
            raise RuntimeError("Renderer not started — call await renderer.start() first.")
        html = self._env.get_template(template_file).render(**context)
        width, height = dimensions
        page = await self._browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=scale
        )
        try:
            await page.set_content(html, wait_until="networkidle")
            await page.evaluate("() => document.fonts.ready")
            return await page.screenshot(
                type="png", omit_background=transparent, animations="disabled"
            )
        finally:
            await page.close()

    async def render(
        self,
        template_variant: str,
        slots: dict[str, Any],
        dimensions: tuple[int, int] = PLATFORM_DIMENSIONS["square"],
        scale: float = 2.0,
    ) -> bytes:
        if self._browser is None:
            raise RuntimeError("Renderer not started — call await renderer.start() first.")
        if template_variant not in TEMPLATES:
            raise KeyError(f"Unknown template_variant: {template_variant!r}")

        html = self.render_html(template_variant, slots)
        width, height = dimensions
        page = await self._browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
        )
        try:
            await page.set_content(html, wait_until="networkidle")
            await page.evaluate("() => document.fonts.ready")  # ensure webfonts painted
            return await page.screenshot(type="png", animations="disabled")
        finally:
            await page.close()


# App-wide singleton; FastAPI's lifespan calls start()/stop().
renderer = Renderer()
