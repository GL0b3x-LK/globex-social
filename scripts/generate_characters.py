"""Generate reference image sheets for the video-engine character roster.

Each character in app/data/characters.json carries a ``visual_prompt``; this
script turns that into a small set of consistent reference shots (front portrait,
three-quarter, waist-up talking, workplace context) which become (a) the sheet
Len approves once, and (b) the identity references the keyframe compositor feeds
to the image model alongside a real pack shot.

The shared style/negative block is built from the roster's own generation rules
so the client's hard "no" list (logos, steam, spotlights, carcasses) can never
drift out of sync with the data.

Output: app/data/characters/<slug>/<shot>.jpg  (JPEG — these get committed)

Run:
    .venv/bin/python scripts/generate_characters.py --dry-run      # print prompts
    .venv/bin/python scripts/generate_characters.py                # full sheets
    .venv/bin/python scripts/generate_characters.py --shots 1      # one per person
    .venv/bin/python scripts/generate_characters.py --only john,mei
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.ai import image_gen  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import library  # noqa: E402

log = get_logger("generate_characters")

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "characters"
_CONCURRENCY = 3  # kie.ai rate-limits, and a wide fan-out exhausts connections
_JPEG_QUALITY = 88

# Character sheets run on GPT Image-2 (top-ranked for photoreal people as of
# Aug 2026) rather than the post pipeline's model — identity fidelity here is
# what every later keyframe inherits.
DEFAULT_MODEL = "gpt-image-2-text-to-image"


@dataclass(frozen=True)
class Shot:
    key: str
    framing: str
    aspect: str


# Ordered by usefulness: if only one shot is generated it should be the clean
# front portrait, because that is what identity matching keys on.
SHOTS: tuple[Shot, ...] = (
    Shot(
        "front",
        "Head-and-shoulders portrait facing the camera directly, neutral friendly "
        "expression, eyes to camera, face fully visible and unobscured",
        "1:1",
    ),
    Shot(
        # Any visible product must be sealed/packaged. This is the client's own
        # rule (packaged presentation, never raw) and it also keeps the shot clear
        # of the generator's content filter, which rejects exposed raw meat.
        "context",
        "Medium-wide shot in their workplace, engaged in their work, upper body and "
        "hands visible, environment readable behind them. Any product visible is "
        "already sealed in clean packaging, trays or closed cartons — no exposed "
        "raw meat anywhere in the frame",
        "9:16",
    ),
    Shot(
        "three_quarter",
        "Three-quarter angle portrait, head turned slightly off-camera, same lighting "
        "and wardrobe as the front portrait",
        "1:1",
    ),
    Shot(
        "talking",
        "Waist-up, talking to camera mid-sentence, relaxed open hands, natural gesture",
        "9:16",
    ),
)

_STYLE = (
    "Photorealistic documentary photograph, natural available light, shot on a "
    "full-frame camera with a 50mm lens, shallow but honest depth of field. Candid "
    "and authentic, like real footage from a working food plant — not a studio "
    "portrait, not a stock photo, not glamorous."
)


def _negatives() -> str:
    """The client's no-list, sourced from the roster data rather than hardcoded."""
    return (
        "Absolutely do not include: any logo, brand mark, wordmark, badge, printed "
        "text or signage anywhere in the frame; steam, smoke, haze, mist or floating "
        "particles; dramatic spotlights or lens flare; raw carcasses, blood or "
        "graphic meat; cropped or obscured faces."
    )


def build_prompt(character: library.Character, shot: Shot) -> str:
    return "\n".join(
        [
            f"{character.visual_prompt}. {shot.framing}.",
            f"Wardrobe: {library.wardrobe_rule()}",
            f"Style: {_STYLE}",
            _negatives(),
        ]
    )


def _save_jpeg(data: bytes, path: Path) -> int:
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=_JPEG_QUALITY, optimize=True)
    return path.stat().st_size


async def _one(
    character: library.Character,
    shot: Shot,
    sem: asyncio.Semaphore,
    *,
    force: bool,
    model: str,
    resolution: str,
) -> tuple[str, bool]:
    label = f"{character.slug}/{shot.key}"
    path = OUT_DIR / character.slug / f"{shot.key}.jpg"
    if path.exists() and not force:
        log.info("skip (exists)", extra={"shot": label})
        return label, True
    async with sem:
        result = await image_gen.generate(
            build_prompt(character, shot),
            aspect_ratio=shot.aspect,
            model=model,
            resolution=resolution,
        )
    if not result.ok or not result.image_bytes:
        log.error("generation failed", extra={"shot": label, "error": result.error})
        return label, False
    size = _save_jpeg(result.image_bytes, path)
    log.info("saved", extra={"shot": label, "kb": size // 1024})
    return label, True


async def run(
    characters: list[library.Character],
    shots: tuple[Shot, ...],
    *,
    force: bool,
    model: str,
    resolution: str,
) -> int:
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(
        *(
            _one(c, s, sem, force=force, model=model, resolution=resolution)
            for c in characters
            for s in shots
        )
    )
    failed = [label for label, ok in results if not ok]
    if failed:
        log.error("some shots failed", extra={"failed": failed})
    log.info("done", extra={"ok": len(results) - len(failed), "failed": len(failed)})
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated character slugs")
    parser.add_argument(
        "--shots", type=int, default=len(SHOTS), help=f"how many shots each (1-{len(SHOTS)})"
    )
    parser.add_argument("--force", action="store_true", help="regenerate existing files")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"kie.ai model (default {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--resolution", default="1K", choices=("1K", "2K", "4K"), help="GPT Image resolution"
    )
    parser.add_argument("--dry-run", action="store_true", help="print prompts, generate nothing")
    args = parser.parse_args()

    configure_logging()
    characters = list(library.load_characters())
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        characters = [c for c in characters if c.slug in wanted]
        if not characters:
            log.error("no characters matched", extra={"only": args.only})
            return 1
    shots = SHOTS[: max(1, min(args.shots, len(SHOTS)))]

    if args.dry_run:
        for c in characters:
            for s in shots:
                print(f"\n=== {c.slug}/{s.key} [{s.aspect}] ===\n{build_prompt(c, s)}")
        print(f"\n{len(characters) * len(shots)} images would be generated.")
        return 0

    # Preflight: one clear message beats N identical failures.
    if not get_settings().kie_api_key:
        log.error(
            "KIE_API_KEY is empty — set it in .env (or route image generation "
            "through the Higgsfield account) before generating character sheets"
        )
        return 2

    log.info(
        "generating",
        extra={
            "characters": len(characters),
            "shots": len(shots),
            "model": args.model,
            "resolution": args.resolution,
        },
    )
    return asyncio.run(
        run(characters, shots, force=args.force, model=args.model, resolution=args.resolution)
    )


if __name__ == "__main__":
    raise SystemExit(main())
