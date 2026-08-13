"""Host the WHOLE curated asset pool and record each URL on pool.json.

``upload_product_shots.py`` only ever hosted the shots attached to a product in
products.json — 16 files out of the hundred in the pool, all of them poultry
packaging. Everything else (lamb, beef, fish, fries, grains, the brand and
quality-control frames) existed only on disk, which meant the pool could be
rendered into a template but never handed to anything that fetches by URL: the
image models and the video compositor take a reference URL, not a local path.

This uploads every asset under ``pool/<file>`` and writes the resulting public
URL back onto its pool.json entry, so the bank is addressable from anywhere.

Idempotent: uploads overwrite and the paths are deterministic, so re-running
after adding shots just fills in the new ones.

Run:
    .venv/bin/python scripts/upload_asset_pool.py --dry-run
    .venv/bin/python scripts/upload_asset_pool.py
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

log = get_logger("upload_asset_pool")

POOL_JSON = library.ASSET_POOL / "pool.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-upload assets that already carry a URL",
    )
    args = parser.parse_args()

    configure_logging()
    doc = json.loads(POOL_JSON.read_text(encoding="utf-8"))
    assets = doc["assets"]

    uploaded = skipped = missing = 0
    for asset in assets:
        name = asset["file"]
        path = library.ASSET_POOL / name
        if not path.exists():
            log.warning("missing asset on disk", extra={"file": name})
            missing += 1
            continue
        if asset.get("url") and not args.force:
            skipped += 1
            continue
        if args.dry_run:
            log.info("would upload", extra={"file": name})
            uploaded += 1
            continue
        asset["url"] = storage.upload_bytes(f"pool/{name}", path.read_bytes(), "image/jpeg")
        uploaded += 1

    if not args.dry_run:
        POOL_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log.info(
        "asset pool upload done",
        extra={
            "uploaded": uploaded,
            "already_hosted": skipped,
            "missing": missing,
            "total": len(assets),
        },
    )
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
