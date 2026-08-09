"""End-to-end video run against the REAL services, delivered to a REAL WhatsApp.

Unlike ``make_video.py`` (which prints the WhatsApp side), this drives the exact
production code path — Twilio included — so a green run here is proof the whole
chain works, not a simulation of it:

    request -> Opus writes the directed script -> WhatsApp
            -> keyframes (kie) -> voice (ElevenLabs)
            -> clips (Higgsfield) -> ffmpeg cut + end slide
            -> Supabase -> WhatsApp video preview

``--failover`` adds a test-only safety net: if the configured provider refuses a
clip (an empty credit wallet being the obvious case), that one scene is retried
on the other vendor so a transport failure upstream still proves the rest of the
pipeline instead of hiding it. Production has no such fallback by design — a
vendor problem should be visible, not silently papered over.

    .venv/bin/python scripts/e2e_video.py "a video of Mei ..." --failover
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import posts as posts_db  # noqa: E402
from app.db import videos as videos_db  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.video import providers  # noqa: E402
from app.workflows import video as flow  # noqa: E402

log = get_logger("e2e_video")


class Failover(providers.VideoGenProvider):
    """Try the primary vendor; on refusal, retry that scene on the backup.

    Records what actually served each scene so the run can be reported honestly
    rather than as an undifferentiated success.
    """

    def __init__(self, primary: providers.VideoGenProvider, backup: providers.VideoGenProvider):
        self.primary, self.backup = primary, backup
        self.served: list[tuple[str, str]] = []  # (vendor, note)

    async def _try(self, call: str, *args) -> providers.ClipResult:
        result = await getattr(self.primary, call)(*args)
        if result.ok:
            self.served.append((type(self.primary).__name__, call))
            return result
        log.warning("primary refused; falling back", extra={"error": (result.error or "")[:160]})
        self.served.append((type(self.primary).__name__ + " FAILED", result.error or ""))
        backup = await getattr(self.backup, call)(*args)
        if backup.ok:
            self.served.append((type(self.backup).__name__, call))
        return backup

    async def speaking_scene(self, keyframe_url: str, audio_url: str, prompt: str):
        return await self._try("speaking_scene", keyframe_url, audio_url, prompt)

    async def broll_scene(self, keyframe_url: str, prompt: str, seconds: float):
        return await self._try("broll_scene", keyframe_url, prompt, seconds)


async def run(request: str, *, phone: str, failover: bool) -> int:
    settings = get_settings()
    print(f"provider : {settings.video_provider}")
    print(f"phone    : {phone}")
    print(f"request  : {request}\n")

    tracker: Failover | None = None
    if failover:
        tracker = Failover(providers.HiggsfieldProvider(), providers.KieProvider())
        providers.get_provider = lambda: tracker  # type: ignore[assignment]
        print("failover : Higgsfield -> kie.ai (test harness only)\n")

    # Stage 1 — the real entry point: resolve, direct, send the script for approval.
    question = await flow.start(phone, request)
    if question:
        print(f"needs an answer first: {question}")
        return 1

    row = await asyncio.to_thread(lambda: videos_db.latest_for(phone))
    if row is None:
        print("no video row was created")
        return 1
    video_id = str(row["id"])
    print(f"video id : {video_id}\nscript sent to WhatsApp. Producing…\n")

    # Stage 2 — what "approve" triggers: frames, voice, clips, cut, preview.
    await flow.produce(video_id, phone)

    meta = videos_db.meta(await asyncio.to_thread(lambda: posts_db.get(video_id)) or {})
    print("\n=== RESULT ===")
    print("stage   :", meta.get("stage"))
    print("master  :", meta.get("master_url"))
    print("preview :", meta.get("preview_url"))
    print("spend   : $", meta.get("spend"))
    if tracker:
        print("\nvendors :")
        for vendor, note in tracker.served:
            print(f"  {vendor:<26} {note[:110]}")
    return 0 if meta.get("master_url") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--phone", default="", help="defaults to AUTHORIZED_NUMBERS[0]")
    parser.add_argument("--failover", action="store_true", help="retry refused clips on kie.ai")
    args = parser.parse_args()

    configure_logging()
    allowed = get_settings().authorized_numbers_list
    phone = args.phone or (allowed[0] if allowed else "")
    if not phone:
        print("no phone: set AUTHORIZED_NUMBERS or pass --phone")
        return 1
    return asyncio.run(run(args.request, phone=phone, failover=args.failover))


if __name__ == "__main__":
    raise SystemExit(main())
