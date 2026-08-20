"""Re-send previews for posts already waiting on approval.

WhatsApp only accepts a free-form business message inside 24 hours of the
recipient's last inbound message, and the Twilio sandbox additionally only talks
to numbers that have run `join <code>`. Miss either and Twilio returns a message
SID, reports "queued", and then fails the message asynchronously — the post is
generated and stored perfectly, it just never arrives.

Nothing needs regenerating in that case. This re-sends the existing rendered
image to the current recipients, so re-opening a window costs no tokens and no
credits.

    .venv/bin/python scripts/resend_pending.py            # what would go, to whom
    .venv/bin/python scripts/resend_pending.py --send
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import posts as posts_db  # noqa: E402
from app.logging_config import configure_logging, get_logger  # noqa: E402
from app.messaging import conversation, twilio_client  # noqa: E402
from app.messaging.conversation import ConversationState  # noqa: E402
from app.workflows import scheduled  # noqa: E402

log = get_logger("resend_pending")


def pending() -> list[dict]:
    """Posts awaiting a yes, NEWEST first — a re-send is almost always about the
    one that just failed to arrive, not a draft from last week."""
    rows = posts_db.list_by_status("pending_approval")
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)


async def resend(post: dict, recipients: list[str]) -> int:
    image_url = post.get("image_url")
    if not image_url:
        log.warning("no image to resend", extra={"post_id": post["id"]})
        return 0
    meta = post.get("render_meta") or {}
    title = ((meta.get("calendar") or {}).get("title")) or "Scheduled post"
    caption = f"🧪 {title}\n\n{post.get('caption') or ''}".strip()

    delivered = 0
    for phone in recipients:
        try:
            await conversation.transition(
                phone,
                state=ConversationState.AWAITING_APPROVAL,
                current_post_id=post["id"],
            )
            # send_preview, not send_media: a preview is owed precisely because
            # delivery failed, and by now the 24-hour window is usually shut —
            # a free-form re-send just fails again with 63016.
            await twilio_client.send_preview(
                phone,
                caption,
                image_url,
                identity=title,
                caption=str(post.get("caption") or ""),
                post_id=post["id"],
            )
            delivered += 1
            print(f"  sent -> {phone}")
        except Exception as exc:  # noqa: BLE001 — one bad number is not a failed resend
            print(f"  FAILED -> {phone}: {str(exc)[:120]}")
    return delivered


async def run(*, send: bool, limit: int, post_id: str = "") -> int:
    rows = [r for r in pending() if r["id"] == post_id] if post_id else pending()[:limit]
    recipients = scheduled.approver_phones()
    print(f"{len(rows)} post(s) awaiting approval; recipients: {', '.join(recipients)}\n")
    for post in rows:
        meta = post.get("render_meta") or {}
        title = ((meta.get("calendar") or {}).get("title")) or "(on-demand)"
        print(f"- {title}  [{post['id']}]")
        if send:
            await resend(post, recipients)
    if not send:
        print("\n(dry run — pass --send to actually deliver)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="actually send")
    parser.add_argument("--limit", type=int, default=1, help="most recent N (default 1)")
    parser.add_argument("--post", default="", help="one post id, ignoring --limit")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(run(send=args.send, limit=args.limit, post_id=args.post))


if __name__ == "__main__":
    raise SystemExit(main())
