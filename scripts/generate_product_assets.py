"""Generate the Globex product image library — the still assets videos are built from.

Every shot is generated FROM the real photograph we already hold, never from a
description, so the packaging, the label artwork and the chick logo come out of a
photograph rather than out of the model's imagination. That is the whole reason
these are `image-to-image` calls: the client's standing rule is that no model
ever redraws Globex packaging.

Two client rules are enforced here in code rather than trusted to the prompt:

* products marked ``never_raw_hero`` are never fronted with unpackaged raw
  product (Len rejected graphic carcass imagery), so those get the packaged
  treatment of each shot instead;
* the forbidden claims — Halal, "90+ countries", "inspected by hand" — never
  reach the prompt, so they can never be rendered into an image.

    .venv/bin/python scripts/generate_product_assets.py                 # everything
    .venv/bin/python scripts/generate_product_assets.py --only duck-retail
    .venv/bin/python scripts/generate_product_assets.py --shots hero --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import image_gen  # noqa: E402
from app.db import storage  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import library  # noqa: E402

log = get_logger("product_assets")

OUT_DIR = Path.home() / "Downloads" / "globex-product-assets"

# Two models, chosen by what the shot has to protect.
#
# GPT Image renders loose product beautifully, but it REDRAWS packaging: asked
# for the duck retail pack it invented "GLOBEX FREE RANGE" on one run and
# "GLOBEX FOODS / Product of South Africa" on the next, when the real pack says
# "PREMIUM DUCK / FROZEN WHOLE DUCK" in four languages. The client called out
# invented packaging by name, so any shot with a label in it goes to
# nano-banana, which holds the reference artwork instead of reinterpreting it.
RAW_MODEL = "gpt-image-2-image-to-image"
LABEL_MODEL = "nano-banana-2"
# 4:5 was the brief, but kie currently refuses it: "generation for 4:5 and 5:4
# aspect ratios is temporarily unavailable". 3:4 is the nearest portrait it will
# take, and crops to 4:5 without losing the subject.
ASPECT = "3:4"
RESOLUTION = "2K"
CONCURRENCY = 3  # 4+ exhausts the local connection pool mid-batch

# Lines with no carton photograph of their own borrow the generic Globex export
# carton, which is what they actually ship in.
GENERIC_CARTON = "globex-brand-carton"

# The brand's real values, from globexusa.com and confirmed by the client. The
# Pantone conversions (#002D72 / #5BC2E7) that circulated in the original
# proposal are WRONG and were rejected — do not reintroduce them here.
NAVY = "#002D70"
CYAN = "#5BC0DE"

# Forbidden claims never appear below. "Halal", "90+ countries" and "inspected by
# hand" are on the client's no-say list, and a prompt that names them risks the
# model rendering them into the image as text.
_STYLE = """Globex blue is the through-line. Cool blue lighting and blue accents tie every
shot to the brand and reinforce the frozen, cold-chain story.
Premium, clean and appetizing. Never clinical, never a wet market. Think high-end
frozen food brand, not a butcher's back room.
Trust cues to convey visually, never as text: all natural, deep-frozen, Quality
Control, trading since 1993."""

# Only ever sent with a shot that genuinely has packaging in it. Sending it with
# a bare-product shot is what made GPT Image invent a whole Globex retail pack —
# navy label, globe-and-chicken logo, "TRADING SINCE 1993" — around loose breasts
# whose reference photograph had no packaging in it at all.
_PACKAGING_FIDELITY = """The Globex logo and ALL packaging label artwork must stay EXACTLY as in the
reference photograph — same colours, same layout, same lettering, same languages.
Do not redraw, restyle, re-letter, translate, simplify or invent any label,
wordmark, logo, badge, seal or text."""

_NO_PACKAGING = """There is NO packaging anywhere in this image. The product is bare. Do not add a
bag, tray, film, box, sticker, label, badge, seal, logo or ANY printed text — not
on the product, not on the surface, not in the background. Inventing Globex
branding is the single worst thing you can do here."""

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
    key: str
    prefers: str  # "product" (loose product) or "pack" (packaging) reference
    packaged: bool  # True when the product must appear sealed/packaged
    body: str


# The two looks the client already approved, plus the cold-store establisher.
# Each exists in a raw and a packaged form so a `never_raw_hero` product can have
# the same shot without the carcass.
SHOTS: list[Shot] = [
    Shot(
        key="hero",
        prefers="product",
        packaged=False,
        body=(
            "Using the attached image as the exact reference for the product's shape, "
            "colour, texture and proportions, create a premium commercial food "
            "photograph of it as a high-end advertising hero shot. The product rests on "
            "a clean brushed-stainless surface against a smooth, seamless deep-blue "
            f"gradient background ({NAVY} navy falling away to a lighter {CYAN} glow). "
            "Cinematic studio lighting with a soft key from the upper left and a crisp "
            "rim light separating the product from the background. It looks fresh, "
            "plump and flawless with a subtle natural sheen. The air is completely "
            "clear. Shallow depth of field, 85mm lens look, slight three-quarter front "
            "angle. Appetizing and premium. No packaging in frame."
        ),
    ),
    Shot(
        key="hero-packed",
        prefers="pack",
        packaged=True,
        body=(
            "Using the attached image as the exact reference for the packaging, its "
            "label artwork and its proportions, create a premium commercial product "
            "photograph of that sealed pack as a high-end advertising hero shot. It "
            "stands on a clean brushed-stainless surface against a smooth, seamless "
            f"deep-blue gradient background ({NAVY} navy falling away to a lighter "
            f"{CYAN} glow). Cinematic studio lighting, soft key from the upper left, "
            "crisp rim light separating the pack from the background. The pack is "
            "clean and dry, the air completely clear — the cold reads from the blue "
            "light, not from weather. Shallow depth of field, 85mm lens look, slight "
            "three-quarter front angle. The label must be pin-sharp and identical to "
            "the reference."
        ),
    ),
    Shot(
        key="qc-hands",
        prefers="product",
        packaged=False,
        body=(
            "Using the attached image as the exact reference for the product, create a "
            "premium commercial photograph of a food-quality inspector's blue-gloved "
            "hands holding it up, gently presenting it to camera. Setting: a spotless "
            "modern food-processing facility, softly blurred stainless steel and cool "
            "blue tones behind (bokeh). The nitrile gloves are clean Globex blue "
            f"({CYAN}). The product is held bare in the bare gloved hands — no bag, "
            "no tray, no film, no label. It looks pristine and high quality. Soft, "
            "flattering commercial lighting that conveys care, hygiene and Quality "
            "Control. Shallow depth of field, 50mm lens, eye level, hands and product "
            "in sharp focus."
        ),
    ),
    Shot(
        key="qc-hands-packed",
        prefers="pack",
        packaged=True,
        body=(
            "Using the attached image as the exact reference for the packaging and its "
            "label artwork, create a premium commercial photograph of a food-quality "
            "inspector's blue-gloved hands holding that sealed pack up and presenting "
            "it to camera. Setting: a spotless modern food-processing facility, softly "
            "blurred stainless steel and cool blue tones behind (bokeh). The nitrile "
            f"gloves are clean Globex blue ({CYAN}). Soft, flattering commercial "
            "lighting that conveys care, hygiene and Quality Control. Shallow depth of "
            "field, 50mm lens, eye level. The label must be sharp and identical to the "
            "reference — do not redraw it."
        ),
    ),
    Shot(
        key="cold-store",
        prefers="pack",
        packaged=True,
        body=(
            "Using the attached image as the exact reference for the carton and its "
            "printed artwork, create a premium commercial photograph of those cartons "
            "stacked neatly on a pallet inside a modern cold store. Deep cool blue "
            f"ambient light ({NAVY} shadows lifting to {CYAN} highlights), clean "
            "concrete floor, racking receding into soft focus, perfectly clear air. "
            "The nearest carton is sharp and its printing is identical to the "
            "reference. Wide-ish 35mm lens, slight low angle so the stack feels "
            "substantial. Industrial but premium — the cold chain, handled well."
        ),
    ),
]

SHOTS_BY_KEY = {s.key: s for s in SHOTS}

# Every product gets three shots: a hero, a pair of hands, and an establisher.
# Which variant depends on whether the product may be shown unpackaged.
RAW_SET = ["hero", "qc-hands", "cold-store"]
PACKED_SET = ["hero-packed", "qc-hands-packed", "cold-store"]


def shots_for(product: library.Product) -> list[Shot]:
    """The three shots this product may legitimately have.

    A product the client will not allow unpackaged gets the packaged variant of
    the same look rather than being skipped — the library still ends up complete.
    """
    rules = product.visual_rules or {}
    packed = (
        bool(rules.get("never_raw_hero"))  # Len rejected graphic carcass imagery
        or bool(rules.get("packaging_required"))  # duck must be in its real livery
        or product.category == "packaging"  # the carton IS the product
        or not product.product_shot_files  # nothing loose to photograph
    )
    return [SHOTS_BY_KEY[k] for k in (PACKED_SET if packed else RAW_SET)]


def model_for(shot: Shot) -> str:
    """Which generator this shot needs.

    Anything with Globex packaging in frame must hold its label artwork, so it
    goes to the reference-preserving model; loose product has no label to
    protect and renders better on GPT Image.
    """
    return LABEL_MODEL if shot.packaged else RAW_MODEL


def reference_for(product: library.Product, shot: Shot) -> str | None:
    """The real photograph this shot is built from, honouring what it needs.

    Hosted rather than local: kie fetches the reference by URL, so these are the
    same public objects the video keyframes already composite from. A shot of
    cartons falls back to the generic Globex carton when a line has no pack shot
    of its own — the same documented stand-in products.json already uses.
    """
    if shot.prefers == "pack":
        if product.pack_shot_files:
            return storage.public_url(f"products/{product.slug}/{product.pack_shot_files[0]}")
        return storage.public_url(f"products/{GENERIC_CARTON}/brand-box.jpg")
    files = product.product_shot_files or product.pack_shot_files
    if not files:
        return None
    return storage.public_url(f"products/{product.slug}/{files[0]}")


def build_prompt(product: library.Product, shot: Shot) -> str:
    """Assemble the prompt, saying the right thing about packaging for this shot.

    Which packaging clause goes in is decided here rather than written into every
    shot, because getting it wrong in either direction is a client rejection:
    invented branding on one side, unpackaged carcass on the other.
    """
    subject = f"The product is {product.name}: {product.description}"
    if shot.packaged:
        subject += (
            " It must appear sealed in its real packaging exactly as photographed — "
            "never loose, never in invented packaging."
        )
        rule = _PACKAGING_FIDELITY
    else:
        subject += " It is shown bare, exactly as in the reference photograph."
        rule = _NO_PACKAGING
    return "\n\n".join([_STYLE, subject, shot.body, rule, _TECH])


def _extension(data: bytes) -> str:
    return ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"


async def one(
    product: library.Product,
    shot: Shot,
    sem: asyncio.Semaphore,
    *,
    force: bool,
    model: str | None,
    out_dir: Path,
) -> tuple[str, bool, str]:
    label = f"{product.slug}/{shot.key}"
    folder = out_dir / product.slug
    existing = [p for p in folder.glob(f"{shot.key}.*")] if folder.exists() else []
    if existing and not force:
        return label, True, "skipped (already there)"

    reference = reference_for(product, shot)
    if reference is None:
        return label, False, "no reference photograph on file"

    chosen = model or model_for(shot)
    async with sem:
        result = await image_gen.edit_multi(
            [reference],
            build_prompt(product, shot),
            aspect_ratio=ASPECT,
            model=chosen,
            resolution=RESOLUTION,
        )
    if not result.ok or not result.image_bytes:
        return label, False, result.error or "no image returned"

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{shot.key}{_extension(result.image_bytes)}"
    path.write_bytes(result.image_bytes)
    return label, True, f"{len(result.image_bytes) // 1024:>5} KB  {chosen}"


async def run(
    products: list[library.Product],
    keys: list[str] | None,
    *,
    force: bool,
    model: str | None,
    out_dir: Path,
) -> int:
    sem = asyncio.Semaphore(CONCURRENCY)
    jobs = [(p, s) for p in products for s in shots_for(p) if keys is None or s.key in keys]
    print(f"{len(jobs)} images -> {out_dir}\n")

    done = await asyncio.gather(
        *(one(p, s, sem, force=force, model=model, out_dir=out_dir) for p, s in jobs)
    )
    failures = [(lab, note) for lab, ok, note in done if not ok]
    for label, ok, note in done:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:<34} {note}")

    print(f"\n{len(done) - len(failures)}/{len(done)} generated into {out_dir}")
    if failures:
        print("\nnot generated:")
        for label, note in failures:
            print(f"  {label:<34} {note}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="one product slug")
    parser.add_argument("--shots", default="", help="comma-separated shot keys")
    parser.add_argument("--model", default="", help="force one kie model for every shot")
    parser.add_argument("--out", default="", help="output folder (defaults to Downloads)")
    parser.add_argument("--force", action="store_true", help="regenerate what already exists")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, generate nothing")
    args = parser.parse_args()

    configure_logging()
    products = [p for p in library.load_products() if p.status == "active"]
    if args.only:
        products = [p for p in products if p.slug == args.only]
        if not products:
            print(f"no active product called {args.only}")
            return 1
    keys = [k.strip() for k in args.shots.split(",") if k.strip()] or None

    if args.dry_run:
        for product in products:
            for shot in shots_for(product):
                if keys and shot.key not in keys:
                    continue
                ref = reference_for(product, shot)
                mark = "ok " if ref else "NO REF"
                model = args.model or model_for(shot)
                print(
                    f"{mark} {product.slug:<22} {shot.key:<17} {model:<28} "
                    f"{(ref or '').rsplit('/', 1)[-1]}"
                )
        return 0

    out_dir = Path(args.out).expanduser() if args.out else OUT_DIR
    return asyncio.run(
        run(products, keys, force=args.force, model=args.model or None, out_dir=out_dir)
    )


if __name__ == "__main__":
    raise SystemExit(main())
