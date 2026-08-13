"""Widen the packaging half of the asset pool.

The bank held six packaging photographs, three of them the same export carton
shot three ways, against a calendar that runs twenty rotating packaging posts.
The testers saw the consequence before anyone measured it: the same box, over
and over.

Every shot here is generated FROM a real client photograph, never from a
description — the same standing rule the product generator works under. Globex
packaging is not the model's to draw: the panel colour, the green base band, the
rooster mark, the multilingual label copy and the Halal badge all come out of a
photograph. What changes is the SETTING (a line, a container, a pallet, a retail
shelf), which is the part no photograph of ours currently covers.

That rule is also the limit of this script. The client's five colourways
(BLACK, BLUE 288C, RED 1795C, GREEN 349C and the duck orange) arrived as a
0-byte ZIP and were never re-sent — see docs/missing_assets.md — so we hold real
photographs of the blue chicken and orange duck liveries only. Beef in black,
pork in red and seafood in green cannot be produced here without inventing
label artwork and re-lettering it into languages we cannot proofread, which is
the one thing the client has explicitly forbidden. Those shots need the artwork.

    .venv/bin/python scripts/generate_packaging_assets.py --dry-run
    .venv/bin/python scripts/generate_packaging_assets.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.ai import image_gen  # noqa: E402
from app.db import storage  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import library  # noqa: E402

log = get_logger("packaging_assets")

POOL_DIR = library.ASSET_POOL
POOL_JSON = POOL_DIR / "pool.json"

MODEL = "nano-banana-2"  # holds a reference; GPT Image redraws branding
ASPECT = "3:4"
RESOLUTION = "2K"
CONCURRENCY = 3
WIDTH, HEIGHT = 1080, 1350
QUALITY = 85

NAVY = "#002D70"
CYAN = "#5BC0DE"

# Verbatim from the product generator. The clause is what stands between a
# faithful pack shot and an invented one, so it is quoted rather than paraphrased.
_FIDELITY = """The Globex logo and ALL packaging label artwork must stay EXACTLY as in the
reference photograph — same colours, same layout, same lettering, same languages,
same badges. Do not redraw, restyle, re-letter, translate, simplify, recolour or
invent any label, wordmark, logo, badge, seal or text. If a surface is not legible
in the reference, leave it out of focus rather than inventing what it says.
Every word already printed on the pack must read EXACTLY as in the reference,
letter for letter — a single altered character (KEEP FROZEN becoming STEP FROZEN)
makes the photograph unusable."""

_TECH = f"""Ultra photorealistic, 8K, advertising quality. Colour palette built around
Globex navy {NAVY} and Globex light blue {CYAN}.
The cold is told by the blue light and the clean surfaces, NOT by weather effects.
DO NOT include: frost, ice crystals, snow, frozen dust, airborne particles of any
kind, steam, smoke, mist, fog, haze, condensation clouds, dramatic spotlights,
light shafts or lens flare. Clean air, clean surfaces.
No added text, no captions, no watermarks, no invented logos, no packaging that is
not in the reference. Aspect ratio {ASPECT}."""


@dataclass(frozen=True)
class Shot:
    name: str  # output filename stem
    refs: tuple[str, ...]  # real pool photographs, in reference order
    tags: tuple[str, ...]
    body: str
    extra_refs: tuple[str, ...] = field(default=())


# Named `pack-globex-*`: the `pack-` prefix is what makes the picker rank these
# ahead of raw product on packaging posts, and `globex` groups them together in
# Karen's printed guide.
SHOTS: list[Shot] = [
    Shot(
        name="pack-globex-carton-line",
        refs=("brand-boxes-closeup.jpg",),
        tags=(
            "packaging",
            "pack",
            "carton",
            "box",
            "brand",
            "globex",
            "line",
            "production",
            "plant",
            "export",
        ),
        body=(
            "Create a premium commercial photograph of these sealed export cartons "
            "moving along a stainless-steel conveyor in a spotless modern packing hall. "
            "The nearest carton is sharp, square to camera and its printing is identical "
            "to the reference; the line recedes into soft focus behind it. Cool blue "
            "ambient light, clean concrete floor, perfectly clear air. 35mm lens, "
            "slight low angle, industrial but premium."
        ),
    ),
    Shot(
        name="pack-globex-carton-container",
        refs=("brand-boxes-closeup.jpg",),
        tags=(
            "packaging",
            "pack",
            "carton",
            "box",
            "brand",
            "globex",
            "container",
            "shipping",
            "export",
            "logistics",
            "port",
        ),
        body=(
            "Create a premium commercial photograph of these cartons stacked wall-to-wall "
            "inside a refrigerated shipping container, loaded and ready to ship. Viewed "
            "from the open container doors looking in, the nearest cartons sharp with "
            "printing identical to the reference, the stack receding into cool blue "
            "shadow. Clean corrugated steel walls, clear air. 24mm lens, eye level, "
            "the scale of a full load."
        ),
    ),
    Shot(
        name="pack-globex-carton-pallet-wrapped",
        refs=("brand-box.jpg",),
        tags=(
            "packaging",
            "pack",
            "carton",
            "box",
            "brand",
            "globex",
            "pallet",
            "warehouse",
            "storage",
            "logistics",
            "export",
        ),
        body=(
            "Create a premium commercial photograph of these cartons stacked on a wooden "
            "pallet and shrink-wrapped in clear film, standing ready for despatch on a "
            "clean warehouse floor. The printing on the front cartons reads clearly "
            "through the film and is identical to the reference. Cool blue ambient "
            "light, racking soft-focused behind, clear air. 35mm lens, slight low angle."
        ),
    ),
    Shot(
        name="pack-globex-carton-stack-hero",
        refs=("brand-boxes-closeup.jpg",),
        tags=("packaging", "pack", "carton", "box", "brand", "globex", "hero", "export"),
        body=(
            "Create a high-end advertising hero photograph of three of these cartons "
            "stacked neatly on a clean brushed-stainless surface against a smooth, "
            f"seamless deep-blue gradient background ({NAVY} navy falling away to a "
            f"lighter {CYAN} glow). Cinematic studio lighting, soft key from the upper "
            "left, crisp rim light separating the stack from the background. Every "
            "printed surface pin-sharp and identical to the reference. 85mm lens look, "
            "slight three-quarter front angle."
        ),
    ),
    Shot(
        name="pack-globex-carton-seal",
        refs=("brand-boxes-closeup.jpg",),
        tags=(
            "packaging",
            "pack",
            "carton",
            "box",
            "brand",
            "globex",
            "hands",
            "quality",
            "control",
            "inspection",
            "plant",
        ),
        body=(
            "Create a premium commercial photograph of a worker's blue-gloved hands "
            "closing and taping the lid of one of these cartons on a stainless-steel "
            f"bench. The nitrile gloves are clean Globex blue ({CYAN}). The carton's "
            "printed face is toward camera, sharp and identical to the reference. "
            "Spotless modern packing hall softly blurred behind, cool blue tones, clear "
            "air. 50mm lens, eye level, hands and carton in sharp focus. Conveys care "
            "and Quality Control."
        ),
    ),
    Shot(
        name="pack-globex-retail-shelf-chicken",
        refs=("pack-chicken-breast.jpg",),
        tags=(
            "packaging",
            "pack",
            "retail",
            "shelf",
            "chicken",
            "breast",
            "poultry",
            "brand",
            "globex",
            "supermarket",
            "consumer",
        ),
        body=(
            "Create a premium commercial photograph of several of these retail packs "
            "arranged face-out on a clean chilled supermarket shelf, as a shopper would "
            "see them. The front pack is sharp and its label is identical to the "
            "reference; the row recedes into soft focus. Cool even retail lighting, "
            "clean white shelf, clear air. 50mm lens, slight three-quarter angle."
        ),
    ),
    Shot(
        name="pack-globex-retail-shelf-duck",
        refs=("pack-duck-retail.jpg",),
        tags=(
            "packaging",
            "pack",
            "retail",
            "shelf",
            "duck",
            "brand",
            "globex",
            "supermarket",
            "consumer",
            "gift",
        ),
        body=(
            "Create a premium commercial photograph of several of these retail duck "
            "packs arranged face-out on a clean chilled supermarket shelf, as a shopper "
            "would see them. The front pack is sharp and its label is identical to the "
            "reference; the row recedes into soft focus. Cool even retail lighting, "
            "clean white shelf, clear air. 50mm lens, slight three-quarter angle."
        ),
    ),
    Shot(
        name="pack-globex-retail-hands",
        refs=("pack-chicken-breast.jpg",),
        tags=(
            "packaging",
            "pack",
            "retail",
            "chicken",
            "breast",
            "poultry",
            "brand",
            "globex",
            "hands",
            "quality",
            "control",
            "consumer",
        ),
        body=(
            "Create a premium commercial photograph of a food-quality inspector's "
            "blue-gloved hands holding this retail pack up and presenting it to camera, "
            "label square to the lens. Setting: a spotless modern food-processing "
            f"facility, softly blurred stainless steel and cool blue tones behind. The "
            f"nitrile gloves are clean Globex blue ({CYAN}). The label is pin-sharp and "
            "identical to the reference. 50mm lens, eye level, shallow depth of field."
        ),
    ),
]


def build_prompt(shot: Shot) -> str:
    return "\n\n".join([shot.body, _FIDELITY, _TECH])


def reference_urls(shot: Shot) -> list[str]:
    """The hosted originals. kie fetches by URL, so these are the pool objects."""
    return [storage.public_url(f"pool/{name}") for name in shot.refs]


def to_pool_jpeg(data: bytes, dest: Path) -> int:
    """Centre-crop the 3:4 still to the 4:5 the posts render at, as JPEG."""
    from io import BytesIO

    with Image.open(BytesIO(data)) as im:
        im = im.convert("RGB")
        target = WIDTH / HEIGHT
        if im.width / im.height > target:
            new_w = int(im.height * target)
            box = ((im.width - new_w) // 2, 0, (im.width + new_w) // 2, im.height)
        else:
            new_h = int(im.width / target)
            box = (0, (im.height - new_h) // 2, im.width, (im.height + new_h) // 2)
        im = im.crop(box).resize((WIDTH, HEIGHT), Image.LANCZOS)
        im.save(dest, "JPEG", quality=QUALITY, optimize=True)
    return dest.stat().st_size


async def render(shot: Shot, semaphore: asyncio.Semaphore) -> bool:
    urls = reference_urls(shot)
    prompt = build_prompt(shot)
    async with semaphore:
        result = (
            await image_gen.edit_multi(
                urls, prompt, model=MODEL, aspect_ratio=ASPECT, resolution=RESOLUTION
            )
            if len(urls) > 1
            else await image_gen.edit(urls[0], prompt, aspect_ratio=ASPECT)
        )
    if not result.ok or not result.image_bytes:
        log.error("shot failed", extra={"shot": shot.name, "error": result.error})
        return False
    size = to_pool_jpeg(result.image_bytes, POOL_DIR / f"{shot.name}.jpg")
    log.info("shot ready", extra={"shot": shot.name, "kb": size // 1024})
    return True


def record(names: list[str]) -> None:
    """Add the new shots to pool.json, hosting each one as it goes in.

    Hosted here rather than in a later pass: an asset in the pool without a URL
    is one the image model cannot be handed as a reference, and the gap between
    the two steps is exactly where the last hundred lost theirs.
    """
    doc = json.loads(POOL_JSON.read_text(encoding="utf-8"))
    by_file = {a["file"]: a for a in doc["assets"]}
    for shot in SHOTS:
        file = f"{shot.name}.jpg"
        if shot.name not in names:
            continue
        path = POOL_DIR / file
        entry = by_file.get(file, {"file": file})
        entry.update(
            {
                "file": file,
                "tags": sorted(set(shot.tags)),
                "generated": True,
                "url": storage.upload_bytes(f"pool/{file}", path.read_bytes(), "image/jpeg"),
            }
        )
        by_file[file] = entry
    doc["assets"] = sorted(by_file.values(), key=lambda a: a["file"])
    POOL_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def run(shots: list[Shot]) -> int:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(render(s, semaphore) for s in shots))
    done = [s.name for s, ok in zip(shots, results, strict=True) if ok]
    if done:
        record(done)
    log.info("packaging assets done", extra={"rendered": len(done), "asked": len(shots)})
    return 0 if len(done) == len(shots) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="comma-separated shot names")
    args = parser.parse_args()

    configure_logging()
    shots = SHOTS
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        shots = [s for s in SHOTS if s.name in wanted]
        if not shots:
            print(f"no shot matches {sorted(wanted)}")
            return 1

    if args.dry_run:
        for shot in shots:
            print(f"{shot.name:<38} <- {', '.join(shot.refs)}")
        return 0
    return asyncio.run(run(shots))


if __name__ == "__main__":
    raise SystemExit(main())
