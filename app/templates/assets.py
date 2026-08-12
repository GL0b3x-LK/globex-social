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

# Self-hosted weights in assets/fonts (no external font CDN at runtime).
#
# The four families below are the ones the designer actually used, confirmed by
# him on 2026-08-12 with weights and sources. They are NOT what the build
# originally inferred: the typefaces had been read off the flattened reference
# PNGs, and three of the four guesses were wrong. Two of these are retail fonts
# from Fontshare (Clash Display, Satoshi) that no amount of measuring against a
# PNG would have named. Only MS-3's Inter was right.
#
#   TS-p1-bolddip        Clash Display  500/600/700      Fontshare
#   TS-p2-cut-navyborder Space Grotesk  500/600/700      Google Fonts
#   TS-p3-editorial      Satoshi        400/500/700/900  Fontshare
#   MS-3-anniv-photo     Inter          400-900          Google Fonts
#                        Archivo Black  400              — the seal number only
#
# Montserrat, Poppins and Comfortaa stay for the demo-era templates that still
# reference them (holiday, stats, quote_card and friends).
_STATIC_FAMILIES: dict[str, tuple[int, ...]] = {
    "Clash Display": (500, 600, 700),
    "Space Grotesk": (500, 600, 700),
    "Satoshi": (400, 500, 700, 900),
    "Inter": (400, 500, 600, 700, 800, 900),
    "Archivo Black": (400,),
    "Montserrat": (400, 500, 600, 700, 800, 900),
    "Poppins": (400, 500, 600, 700, 800),
    "Comfortaa": (400, 500, 600, 700),
}

# NB: take STATIC per-weight files, not the variable font Google's css2 API
# hands back. A variable file pinned to one font-weight renders every weight
# identically, and it does so silently — Space Grotesk 600 and 700 came out
# pixel-identical, and a "900" that was really the 400 default rendered LIGHTER
# than 800. Fontsource (cdn.jsdelivr.net/npm/@fontsource/...) serves statics.


def _slug(family: str) -> str:
    """'Clash Display' -> 'clashdisplay', matching the filenames on disk."""
    return family.lower().replace(" ", "")


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
    for family, weights in _STATIC_FAMILIES.items():
        for weight in weights:
            uri = _data_uri(FONTS_DIR / f"{_slug(family)}-{weight}.woff2", "font/woff2")
            faces.append(
                f"@font-face{{font-family:'{family}';font-style:normal;"
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
