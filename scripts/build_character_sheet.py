"""Build the character contact sheet — the artefact Ilan and Len approve.

Renders every character in the roster with whatever reference shots exist so
far, so it is useful before the set is complete (missing shots show as
placeholders rather than breaking the layout). Uses the project's own Playwright
renderer, so the sheet carries the real brand colours, Poppins and the official
logo instead of looking like a debug dump.

Output: app/data/characters/_contact_sheet.png

Run:  .venv/bin/python scripts/build_character_sheet.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.templates.assets import image_data_uri  # noqa: E402
from app.templates.renderer import Renderer  # noqa: E402
from app.video import library  # noqa: E402

log = get_logger("build_character_sheet")

CHAR_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "characters"
OUT_PATH = CHAR_DIR / "_contact_sheet.png"

_ETHNICITY_LABEL = {
    "east_asian": "East Asian",
    "african": "African",
    "caucasian": "Caucasian",
    "latino": "Latino",
    "latina": "Latina",
    "south_asian": "South Asian",
}

NOTE = (
    "<b>Approve or reject each character.</b> These are invented personas, not real people. "
    "An approved character is locked — same face, same voice, every video from then on. "
    "Wardrobe is plain navy food-safety workwear with no logo on clothing: the Globex mark "
    "is added afterwards by the template, never generated. Rejected characters are replaced "
    "before any video is made."
)


def _uri(slug: str, shot: str) -> str | None:
    path = CHAR_DIR / slug / f"{shot}.jpg"
    return image_data_uri(path.read_bytes()) if path.exists() else None


# Order and frame shape for the full four-shot view.
_ALL_SHOTS = (
    ("front", "square"),
    ("three_quarter", "square"),
    ("context", "tall"),
    ("talking", "tall"),
)


def build_context(columns: int, *, all_shots: bool = False) -> dict[str, object]:
    cards = []
    for c in library.load_characters():
        ethnicity = _ETHNICITY_LABEL.get(c.ethnicity, c.ethnicity.replace("_", " ").title())
        card: dict[str, object] = {
            "name": c.name,
            "meta": f"{ethnicity} · {c.gender.title()} · {c.age}",
            "role": c.role,
            "front": _uri(c.slug, "front"),
            "context": _uri(c.slug, "context"),
        }
        if all_shots:
            card["shots"] = [
                {"key": key.replace("_", " "), "shape": shape, "uri": _uri(c.slug, key)}
                for key, shape in _ALL_SHOTS
            ]
        cards.append(card)
    approved = sum(1 for c in library.load_characters() if c.status == "approved")
    return {
        "title": "Globex video characters",
        "subtitle": (
            f"{len(cards)} personas — {approved} approved, each with 4 stored reference "
            "shots and a locked American-English voice. Every video reuses these exact "
            "images; nothing is re-generated from a description."
        ),
        "characters": cards,
        "columns": columns,
        "note": NOTE,
    }


async def build(columns: int, width: int, *, all_shots: bool = False, out: Path = OUT_PATH) -> Path:
    context = build_context(columns, all_shots=all_shots)
    rows = -(-len(library.load_characters()) // columns)
    height = 250 + rows * (520 if all_shots else 430)
    renderer = Renderer()
    await renderer.start()
    try:
        png = await renderer.render_file(
            "_character_sheet.html", context, dimensions=(width, height), scale=2.0
        )
    finally:
        await renderer.stop()
    out.write_bytes(png)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--width", type=int, default=1800)
    parser.add_argument(
        "--all-shots", action="store_true", help="show all four angles per character"
    )
    args = parser.parse_args()

    configure_logging()
    out = CHAR_DIR / ("_all_shots.png" if args.all_shots else "_contact_sheet.png")
    path = asyncio.run(build(args.columns, args.width, all_shots=args.all_shots, out=out))
    log.info("contact sheet written", extra={"path": str(path), "kb": path.stat().st_size // 1024})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
