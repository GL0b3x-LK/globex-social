"""Embed brand assets (fonts, logos) as base64 data URIs for hermetic rendering.

Playwright `set_content` documents have an ``about:blank`` origin and cannot load
``file://`` subresources, so every asset a template needs is inlined as a data URI.
Encodings are cached at first use — the assets never change at runtime.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
LOGOS_DIR = ASSETS_DIR / "logos"

# Montserrat weights self-hosted in assets/fonts (no external font CDN at runtime).
_FONT_WEIGHTS = (400, 500, 600, 700, 800, 900)

# Logo PNGs converted in Phase 0 (transparent). Keys are template-friendly.
_LOGO_FILES = {
    "gman_full": "globex-gman-full.png",
    "gman_full_white": "globex-gman-full-white.png",
    "lockup_side": "globex-lockup-side.png",
    "lockup_side_white": "globex-lockup-side-white.png",
    "wordmark_navy": "globex-wordmark-navy.png",
    "wordmark_navy_white": "globex-wordmark-navy-white.png",
}


def _data_uri(path: Path, mime: str) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


@lru_cache(maxsize=1)
def font_face_css() -> str:
    """A `<style>`-ready block of @font-face rules with woff2 data URIs."""
    faces = []
    for weight in _FONT_WEIGHTS:
        uri = _data_uri(FONTS_DIR / f"montserrat-{weight}.woff2", "font/woff2")
        faces.append(
            "@font-face{font-family:'Montserrat';font-style:normal;"
            f"font-weight:{weight};font-display:block;"
            f"src:url({uri}) format('woff2');}}"
        )
    return "".join(faces)


@lru_cache(maxsize=1)
def all_logos() -> dict[str, str]:
    """Map of template-friendly logo name -> data URI."""
    return {key: _data_uri(LOGOS_DIR / fname, "image/png") for key, fname in _LOGO_FILES.items()}


def image_data_uri(raw: bytes, media_type: str = "image/jpeg") -> str:
    """Inline an arbitrary image (e.g. a Twilio photo) as a data URI for a template slot."""
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{media_type};base64,{b64}"
