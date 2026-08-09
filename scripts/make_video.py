"""Run the video pipeline end to end from the terminal.

Same code path the WhatsApp flow uses, minus the messaging — so it is the honest
way to prove the pipeline works, and the fastest way to debug it when it does
not. WhatsApp replies are printed instead of sent.

    .venv/bin/python scripts/make_video.py "a video of John holding our duck carton
        talking about how we ship it" --script-only
    .venv/bin/python scripts/make_video.py "..." --yes       # actually generate
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import script as script_engine  # noqa: E402
from app.workflows import video as flow  # noqa: E402

log = get_logger("make_video")


class _Printer:
    """Stands in for Twilio so a terminal run shows exactly what the operator sees."""

    async def send_text(self, to: str, body: str, **kw) -> str:
        print(f"\n--- WhatsApp -> {to} ---\n{body}\n")
        return "printed"

    async def send_media(self, to: str, body: str, media_url: str, **kw) -> str:
        print(f"\n--- WhatsApp media -> {to} ---\n{body}\n{media_url}\n")
        return "printed"


async def run(request: str, *, script_only: bool, phone: str) -> int:
    flow.twilio_client = _Printer()  # type: ignore[assignment]

    resolved = await flow.resolve(request)
    if resolved.question:
        print(f"\nNeeds an answer first: {resolved.question}")
        return 1
    print(
        f"\nresolved -> character={resolved.character.name if resolved.character else 'none (VO)'}"
        f" | product={resolved.product.name if resolved.product else 'none'}"
        f" | {resolved.brief.target_seconds}s"
    )

    doc, problems = await script_engine.write_script(
        resolved.brief, resolved.character, resolved.product
    )
    print(flow.script_preview(doc, resolved.character, flow.estimate_cost(doc)))
    if problems:
        print("REMAINING PROBLEMS:", problems)

    if script_only:
        return 0

    from app.db import videos as videos_db

    video = await asyncio.to_thread(videos_db.create, request, phone)
    video_id = str(video["id"])
    await asyncio.to_thread(
        videos_db.patch_meta,
        video_id,
        script=doc.model_dump(mode="json"),
        script_version=1,
        character=resolved.character.slug if resolved.character else None,
        product=resolved.product.slug if resolved.product else None,
        target_seconds=resolved.brief.target_seconds,
        stage="script_review",
    )
    print(f"\nvideo id: {video_id}\nproducing…")
    await flow.produce(video_id, phone)

    meta = videos_db.meta(
        await asyncio.to_thread(lambda: __import__("app.db.posts", fromlist=["get"]).get(video_id))
        or {}
    )
    print("\n=== RESULT ===")
    print("stage :", meta.get("stage"))
    print("master:", meta.get("master_url"))
    print("spend : $", meta.get("spend"))
    return 0 if meta.get("master_url") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--script-only", action="store_true", help="stop before spending")
    parser.add_argument("--yes", action="store_true", help="proceed through generation")
    parser.add_argument("--phone", default="whatsapp:+10000000000")
    args = parser.parse_args()

    configure_logging()
    script_only = args.script_only or not args.yes
    if script_only:
        print("(script only — pass --yes to generate video)")
    return asyncio.run(run(args.request, script_only=script_only, phone=args.phone))


if __name__ == "__main__":
    raise SystemExit(main())
