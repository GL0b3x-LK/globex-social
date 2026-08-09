"""Create one locked ElevenLabs voice per character and record its id.

Mirrors the reference-image flow: a voice is designed ONCE, saved, and its
durable ``voice_id`` written onto the roster. Nothing at video time re-designs a
voice — that would give a different-sounding person each run, and the client
rejected accent drift outright.

Every voice is neutral American English (characters.json _meta.voice_rule);
personas stay distinct by age, register, pace and warmth instead of accent.

Preview audio for each candidate is saved next to the character's images so the
chosen take can be listened to and re-auditioned later.

Run:
    .venv/bin/python scripts/create_voices.py --dry-run       # show descriptions
    .venv/bin/python scripts/create_voices.py --only john     # one character
    .venv/bin/python scripts/create_voices.py                 # whole roster
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import library, voices  # noqa: E402

log = get_logger("create_voices")

CHAR_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "characters"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated character slugs")
    parser.add_argument("--force", action="store_true", help="replace an existing voice_id")
    parser.add_argument("--dry-run", action="store_true", help="print descriptions only")
    args = parser.parse_args()

    configure_logging()

    # The audition line is client-facing copy: hold it to the same no-say rules.
    banned = library.banned_terms_in(voices.PREVIEW_TEXT)
    if banned:
        log.error("preview text uses forbidden claims", extra={"terms": banned})
        return 1

    roster = list(library.load_characters())
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        roster = [c for c in roster if c.slug in wanted]
        if not roster:
            log.error("no characters matched", extra={"only": args.only})
            return 1

    doc = json.loads(library.CHARACTERS_PATH.read_text(encoding="utf-8"))
    by_slug = {c["slug"]: c for c in doc["characters"]}

    created = 0
    for character in roster:
        description = voices.voice_description(character.voice_direction, character.role)
        if args.dry_run:
            print(f"\n=== {character.slug} ===\n{description}")
            continue
        if character.voice_id and not args.force:
            log.info("skip (already has a voice)", extra={"character": character.slug})
            continue

        try:
            previews = voices.design(description)
            if not previews:
                raise voices.VoiceError("no previews returned")
            # Take the first preview deterministically; re-run with --force to
            # re-audition. Every candidate is saved so a different take can be
            # chosen later without paying to regenerate the set.
            for i, preview in enumerate(previews):
                (CHAR_DIR / character.slug / f"voice_preview_{i}.mp3").write_bytes(preview.audio)
            voice_id = voices.save(
                name=f"Globex — {character.name}",
                description=description,
                generated_voice_id=previews[0].generated_voice_id,
                labels={"project": "globex-video", "character": character.slug},
            )
        except voices.VoiceError as exc:
            log.error("voice creation failed", extra={"character": character.slug, "err": str(exc)})
            continue

        by_slug[character.slug]["voice_id"] = voice_id
        created += 1
        log.info(
            "voice locked",
            extra={"character": character.slug, "voice_id": voice_id, "previews": len(previews)},
        )

    if not args.dry_run and created:
        library.CHARACTERS_PATH.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        library.load_characters.cache_clear()
        library._characters_doc.cache_clear()

    log.info("done", extra={"voices_created": created})  # 'created' is a LogRecord field
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
