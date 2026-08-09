"""Host the character reference shots and record their URLs on the roster.

The whole point of the character library is that a persona is generated ONCE and
then reused: every keyframe passes the stored reference image to the generator,
so John looks like John in his fiftieth video exactly as he did in his first.
Re-running the text prompt instead would produce a different person each time.

This script closes that loop — it uploads each character's shots to Supabase
Storage (public-read, so Higgsfield/kie can fetch them) and writes the resulting
URLs into ``reference_image_urls`` on each record, with the front portrait first
because that is the identity anchor.

Idempotent: uploads use upsert and the URLs are deterministic, so re-running
after adding a shot just extends the list.

Run:
    .venv/bin/python scripts/upload_character_refs.py --dry-run
    .venv/bin/python scripts/upload_character_refs.py
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

log = get_logger("upload_character_refs")


def _ordered_shots(character: library.Character) -> list[Path]:
    """Front portrait first — downstream code takes reference_image_urls[0] as identity."""
    paths = list(character.reference_paths)
    primary = character.primary_reference
    if primary in paths:
        paths.remove(primary)
        paths.insert(0, primary)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show what would upload")
    args = parser.parse_args()

    configure_logging()
    doc = json.loads(library.CHARACTERS_PATH.read_text(encoding="utf-8"))
    by_slug = {c["slug"]: c for c in doc["characters"]}

    total = 0
    for character in library.load_characters():
        shots = _ordered_shots(character)
        if not shots:
            log.warning("no reference shots on disk", extra={"character": character.slug})
            continue
        if args.dry_run:
            log.info(
                "would upload",
                extra={"character": character.slug, "shots": [p.stem for p in shots]},
            )
            total += len(shots)
            continue

        urls = [
            storage.upload_character_reference(character.slug, path.stem, path.read_bytes())
            for path in shots
        ]
        by_slug[character.slug]["reference_image_urls"] = urls
        total += len(urls)
        log.info("uploaded", extra={"character": character.slug, "shots": len(urls)})

    if not args.dry_run:
        library.CHARACTERS_PATH.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        library.load_characters.cache_clear()
        library._characters_doc.cache_clear()

    log.info("done", extra={"images": total, "dry_run": args.dry_run})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
