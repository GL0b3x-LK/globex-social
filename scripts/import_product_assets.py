"""Bring the generated product stills into the post pipeline's photo pool.

The stills are generated at 2K 3:4 and land in Downloads as multi-megabyte PNGs.
Nothing downstream can use them at that size — posts render 4:5 and ship to
Railway inside the repo — so this crops to 1080x1350 and re-encodes as JPEG,
which is what the existing pool assets already are.

They join the SAME pool as the client's real photographs rather than sitting in
a parallel one, so `scheduled.pick_photo_for_text` keeps working untouched. That
picker scores on filename prefix, so the prefix carries the meaning:

* ``pack-`` — Globex packaging is visible; wins product and packaging posts.
* ``prod-`` — bare or plain-tray product; never fronts a brand or holiday post.

    .venv/bin/python scripts/import_product_assets.py --dry-run
    .venv/bin/python scripts/import_product_assets.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import library  # noqa: E402

log = get_logger("import_product_assets")

SRC_DIR = Path.home() / "Downloads" / "globex-product-assets"
POOL_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "asset_pool"
POOL_JSON = POOL_DIR / "pool.json"

# Posts render 4:5; the stills are 3:4, so a little height comes off.
WIDTH, HEIGHT = 1080, 1350
QUALITY = 85

# Shots where Globex packaging is in frame. Everything else is bare product or a
# deliberately unbranded tray.
PACKAGED_SHOTS = {"hero-packed", "qc-hands-packed", "cold-store"}

# What each shot is FOR, in words the calendar's titles and gists actually use.
SHOT_TAGS = {
    "hero": ("product", "hero"),
    "hero-packed": ("packaging", "pack", "retail", "hero"),
    "hero-tray": ("product", "tray", "pack"),
    "qc-hands": ("quality", "control", "inspection", "hands", "plant"),
    "qc-hands-packed": ("quality", "control", "inspection", "hands", "packaging", "pack"),
    "qc-hands-tray": ("quality", "control", "inspection", "hands", "tray"),
    "cold-store": ("cold", "store", "storage", "warehouse", "carton", "pallet", "packaging"),
}

_STOP = {"and", "the", "of", "for", "with", "a", "an", "in", "x", "mm"}


def tags_for(product: library.Product, shot: str) -> list[str]:
    """Words a calendar title or gist would plausibly contain for this image."""
    words: set[str] = set(SHOT_TAGS.get(shot, ()))
    words.add(product.category.split("_")[0])
    for source in (product.name, *product.aliases, product.slug.replace("-", " ")):
        for word in source.lower().replace(",", " ").replace("-", " ").split():
            cleaned = word.strip("()").strip()
            if len(cleaned) > 2 and cleaned not in _STOP:
                words.add(cleaned)
    return sorted(words)


def to_pool_jpeg(src: Path, dest: Path) -> int:
    """Centre-crop to 4:5 and save as JPEG. Returns bytes written."""
    image = Image.open(src)
    if image.mode != "RGB":
        image = image.convert("RGB")

    target = WIDTH / HEIGHT
    w, h = image.size
    if w / h > target:  # too wide — trim the sides
        new_w = int(h * target)
        left = (w - new_w) // 2
        image = image.crop((left, 0, left + new_w, h))
    else:  # too tall — trim top and bottom evenly
        new_h = int(w / target)
        top = (h - new_h) // 2
        image = image.crop((0, top, w, top + new_h))

    image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=QUALITY, optimize=True)
    return dest.stat().st_size


def collect() -> list[tuple[Path, str, library.Product, str]]:
    """(source file, pool filename, product, shot) for everything worth importing."""
    by_slug = {p.slug: p for p in library.load_products()}
    found: list[tuple[Path, str, library.Product, str]] = []
    for folder in sorted(SRC_DIR.iterdir() if SRC_DIR.exists() else []):
        if not folder.is_dir():
            continue
        product = by_slug.get(folder.name)
        if product is None:
            log.warning("no product for folder", extra={"folder": folder.name})
            continue
        for image in sorted(folder.glob("*.png")):
            shot = image.stem
            prefix = "pack" if shot in PACKAGED_SHOTS else "prod"
            found.append((image, f"{prefix}-{product.slug}-{shot}.jpg", product, shot))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    configure_logging()

    if not SRC_DIR.exists():
        print(f"nothing to import: {SRC_DIR} does not exist")
        return 1

    items = collect()
    print(f"{len(items)} stills -> {POOL_DIR}")
    if args.dry_run:
        for _src, name, product, shot in items:
            print(f"  {name:<44} {' '.join(tags_for(product, shot))}")
        return 0

    doc = json.loads(POOL_JSON.read_text(encoding="utf-8"))
    assets: list[dict] = doc["assets"]
    by_file = {a["file"]: a for a in assets}

    total = 0
    for src, name, product, shot in items:
        total += to_pool_jpeg(src, POOL_DIR / name)
        entry = {"file": name, "tags": tags_for(product, shot), "generated": True}
        if name in by_file:
            by_file[name].update(entry)
        else:
            assets.append(entry)
            by_file[name] = entry

    doc["assets"] = sorted(assets, key=lambda a: a["file"])
    POOL_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"imported {len(items)} images, {total // 1024 // 1024} MB, pool now {len(assets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
