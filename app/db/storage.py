"""Supabase Storage: upload rendered post PNGs, return public URLs.

The supabase-py storage client is synchronous, so each call is wrapped in
``asyncio.to_thread`` to keep the FastAPI event loop responsive during uploads.
Blotato fetches the image by URL at publish time, so the bucket is public-read.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from app.db.client import get_supabase
from app.logging_config import get_logger

log = get_logger("app.db.storage")

BUCKET = "post-images"


_UPLOAD_RETRIES = 3


def _upload_sync(
    path: str, data: bytes, content_type: str = "image/png", *, cache_seconds: int = 3600
) -> str:
    """Upload with retries — a dropped connection mid-transfer is common for
    multi-megabyte video and should not lose an expensive generated asset.

    ``cache_seconds`` is the CDN cache the object is served with. Media that
    never changes keeps the default; anything overwritten in place and read
    back (the learned-rules store) must use 0, or every update is invisible
    behind the CDN for up to an hour.
    """
    sb = get_supabase()
    last: Exception | None = None
    for attempt in range(_UPLOAD_RETRIES):
        try:
            sb.storage.from_(BUCKET).upload(
                path,
                data,
                {
                    "content-type": content_type,
                    "cache-control": str(cache_seconds),
                    "upsert": "true",
                },
            )
            return str(sb.storage.from_(BUCKET).get_public_url(path))
        except Exception as exc:  # noqa: BLE001 — retried below, re-raised if terminal
            last = exc
            log.warning(
                "storage upload failed; retrying",
                extra={"path": path, "attempt": attempt + 1, "error": str(exc)[:120]},
            )
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"upload failed after {_UPLOAD_RETRIES} attempts: {last}")


async def upload_png(post_id: str | UUID, png_bytes: bytes, *, suffix: str = "") -> str:
    """Upload a rendered PNG to ``post-images/{post_id}{suffix}.png``; return its public URL.

    ``upsert`` is on so re-rendering after an edit overwrites the same object. ``suffix``
    distinguishes sibling objects for one post — e.g. ``suffix="-raw"`` hosts the raw
    AI-generated image (kept so img2img edits can transform it) alongside the composite.
    """
    return await asyncio.to_thread(_upload_sync, f"{post_id}{suffix}.png", png_bytes)


async def upload_video(post_id: str | UUID, mp4_bytes: bytes) -> str:
    """Upload a rendered MP4 to ``post-images/{post_id}.mp4``; return its public URL."""
    return await asyncio.to_thread(_upload_sync, f"{post_id}.mp4", mp4_bytes, "video/mp4")


def upload_character_reference(slug: str, shot: str, jpeg_bytes: bytes) -> str:
    """Host a character's reference shot; return its public URL.

    These are generated once and then reused forever: every keyframe references
    the hosted image, so a character's face never depends on re-running a prompt.
    Synchronous — this is one-time setup run from a script, not request-path code.
    """
    return _upload_sync(f"characters/{slug}/{shot}.jpg", jpeg_bytes, "image/jpeg")


def public_url(path: str) -> str:
    """The public URL for an object already in the bucket."""
    return str(get_supabase().storage.from_(BUCKET).get_public_url(path))


def upload_bytes(path: str, data: bytes, content_type: str, *, cache_seconds: int = 3600) -> str:
    """Host arbitrary bytes at `path`; return the public URL. Overwrites."""
    return _upload_sync(path, data, content_type, cache_seconds=cache_seconds)


def read_bytes(path: str, *, fresh: bool = False) -> bytes | None:
    """The object at `path`, or None if it does not exist (or cannot be read).

    ``fresh=True`` bypasses the CDN with a cache-busting public GET — required
    for any object that is overwritten in place and read back (the learned-rules
    store), where the plain read served an hour-stale version and a
    read-modify-write on that stale base would silently drop new entries.
    """
    try:
        if fresh:
            from uuid import uuid4

            import httpx

            url = f"{public_url(path)}?fresh={uuid4().hex[:8]}"
            resp = httpx.get(url, timeout=30.0)
            if resp.status_code == 200:
                return resp.content
            return None
        return bytes(get_supabase().storage.from_(BUCKET).download(path))
    except Exception:  # noqa: BLE001 — absence and failure both mean "no data"
        return None


def upload_video_asset(video_id: str, name: str, data: bytes, content_type: str) -> str:
    """Host one artifact of a video under ``videos/{video_id}/{name}``.

    Generation providers fetch inputs by URL, so keyframes and voice tracks must
    be publicly reachable before a clip can be made from them.
    """
    return _upload_sync(f"videos/{video_id}/{name}", data, content_type)


def ensure_bucket() -> None:
    """Create the public ``post-images`` bucket if absent. Idempotent one-time setup."""
    sb = get_supabase()
    names = {b.name for b in sb.storage.list_buckets()}
    if BUCKET not in names:
        sb.storage.create_bucket(BUCKET, options={"public": True})
