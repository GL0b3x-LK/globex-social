"""Host the product pack shots so the keyframe compositor can reference them.

The generator fetches reference images by URL, so a product photo that lives
only on disk cannot be used to keep packaging accurate — which is the entire
reason the real pack shot is passed in rather than described. This uploads the
curated asset pool under ``products/<slug>/`` and records the URLs on the roster.

Idempotent: uploads overwrite, and the URLs are deterministic.

Run:  .venv/bin/python scripts/upload_product_shots.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import storage  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import library  # noqa: E402

log = get_logger("upload_product_shots")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    configure_logging()
    doc = json.loads(library.PRODUCTS_PATH.read_text(encoding="utf-8"))
    by_slug = {p["slug"]: p for p in doc["products"]}

    total = 0
    for product in library.load_products():
        files = (*product.pack_shot_files, *product.product_shot_files)
        urls: list[str] = []
        for name in files:
            path = library.ASSET_POOL / name
            if not path.exists():
                log.warning("missing asset", extra={"product": product.slug, "file": name})
                continue
            if args.dry_run:
                urls.append(f"(would upload) {name}")
                continue
            urls.append(
                storage.upload_bytes(
                    f"products/{product.slug}/{name}", path.read_bytes(), "image/jpeg"
                )
            )
        if not args.dry_run and urls:
            # pack shots first: the compositor takes hero_files[0] as the packaging
            # reference, and packaging always outranks a raw product photo.
            packs = [u for u in urls if Path(u).name in product.pack_shot_files]
            rest = [u for u in urls if u not in packs]
            by_slug[product.slug]["pack_shot_urls"] = packs
            by_slug[product.slug]["product_shot_urls"] = rest
        total += len(urls)
        log.info("uploaded", extra={"product": product.slug, "images": len(urls)})

    if not args.dry_run:
        library.PRODUCTS_PATH.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        library.load_products.cache_clear()
        library._products_doc.cache_clear()

    log.info("done", extra={"images": total, "dry_run": args.dry_run})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
