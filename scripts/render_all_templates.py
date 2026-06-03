"""Render one example PNG per template to docs/template-previews/ for visual review.

Dev utility — the gallery is the Karen-quality eyeball check for Phase 3. Run:

    $env:PYTHONPATH = "<repo root>"
    .venv\\Scripts\\python.exe scripts/render_all_templates.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from pathlib import Path

from app.templates.renderer import Renderer

OUT = Path(__file__).resolve().parent.parent / "docs" / "template-previews"

# Representative slot inputs per template. Sample copy only — never published.
SAMPLES: dict[str, dict[str, object]] = {
    "stats": {
        "eyebrow": "Globex by the numbers",
        "figure": "150",
        "subhead": "Ships on the water right now — moving food across 90+ countries, every day.",
        "stat_items": [
            {"value": "90+", "label": "Countries"},
            {"value": "300+", "label": "Suppliers"},
            {"value": "950+", "label": "Trade partners"},
        ],
    },
    "founding_anniversary": {
        "eyebrow": "Celebrating",
        "figure": "33",
        "figure_unit": "Years",
        "headline": "of connecting the world through food.",
        "subhead": "90+ countries · 300+ suppliers · 950+ trade partners.",
    },
    "holiday": {
        "eyebrow": "Food industry",
        "headline": "National Poultry Day",
        "date_label": "March 19",
        "subhead": "Poultry moves through our network every single day. Today, we tip our hat to it.",
    },
    "holiday_month_long": {
        "month_label": "All month",
        "eyebrow": "Food industry",
        "headline": "National Seafood Month",
        "subhead": "From the dock to 90+ countries — seafood is in our DNA.",
    },
    "trade_show_pre": {
        "eyebrow": "Meet us at",
        "headline": "Gulfood 2027",
        "meta": "Feb 8–12 · Dubai World Trade Centre",
        "booth": "Booth 4521",
        "subhead": "Let's talk sourcing, scale, and what's next for your shelves.",
    },
    "trade_show_during": {
        "eyebrow": "Live at",
        "headline": "Gulfood 2027",
        "subhead": "Come find the Globex team on the floor — Hall 4, Booth 4521.",
    },
    "trade_show_post": {
        "eyebrow": "That's a wrap",
        "headline": "Thank you, Gulfood.",
        "subhead": "To every partner who stopped by — here's to the year ahead.",
    },
    "milestone": {
        "eyebrow": "Celebrating",
        "figure": "25",
        "figure_unit": "Years",
        "name": "Maria Alvarez",
        "role": "Director of Logistics",
        "subhead": "Twenty-five years moving the world's food with precision. Thank you, Maria.",
    },
    "announcement": {
        "eyebrow": "Partnership",
        "headline": "Globex welcomes a new sourcing partner in Southeast Asia.",
        "subhead": "Expanding our reach so your shelves never miss a beat.",
    },
    "product_spotlight": {
        "eyebrow": "Product spotlight",
        "headline": "Poultry, sourced and shipped at scale.",
        "subhead": "Reliable supply across 90+ countries — whole bird, cuts, and further-processed.",
    },
    "promotional": {
        "eyebrow": "Globex",
        "headline": "One partner. 90+ countries. Zero guesswork.",
        "subhead": "Global food trading, handled end to end.",
        "cta": "Start sourcing",
    },
    "branded_packaging": {
        "eyebrow": "Globex packaging",
        "headline": "Built for the journey, branded for trust.",
        "subhead": "Five signature colorways across our protein lines.",
    },
    "custom": {
        "eyebrow": "From the team",
        "headline": "A look behind today's shipment.",
        "subhead": "Copy written from Karen's note and photo.",
    },
}

# Templates that compose over a photo (sample photo injected if Pillow is available).
_PHOTO_TEMPLATES = {"trade_show_during", "custom"}


def _sample_photo() -> str | None:
    """A stand-in 'photo' (navy→cyan gradient) so photo templates preview realistically."""
    try:
        from PIL import Image
    except ModuleNotFoundError:
        return None
    small = Image.new("RGB", (64, 64))
    for y in range(64):
        for x in range(64):
            t = (x + y) / 128
            small.putpixel((x, y), (int(t * 91), int(45 + t * 149), int(114 + t * 117)))
    img = small.resize((1200, 1200), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    photo = _sample_photo()
    r = Renderer()
    await r.start()
    try:
        for variant, slots in SAMPLES.items():
            payload = dict(slots)
            if variant in _PHOTO_TEMPLATES and photo:
                payload["photo"] = photo
            start = time.perf_counter()
            png = await r.render(variant, payload)
            elapsed = (time.perf_counter() - start) * 1000
            (OUT / f"{variant}.png").write_bytes(png)
            print(f"{variant:22} {len(png):>7} bytes  {elapsed:6.0f} ms")
    finally:
        await r.stop()


if __name__ == "__main__":
    asyncio.run(main())
