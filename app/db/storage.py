"""Supabase Storage: upload rendered post PNGs, return public URLs.

The supabase-py storage client is synchronous, so each call is wrapped in
``asyncio.to_thread`` to keep the FastAPI event loop responsive during uploads.
Blotato fetches the image by URL at publish time, so the bucket is public-read.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.db.client import get_supabase

BUCKET = "post-images"


def _upload_sync(path: str, data: bytes, content_type: str = "image/png") -> str:
    sb = get_supabase()
    sb.storage.from_(BUCKET).upload(
        path,
        data,
        {"content-type": content_type, "cache-control": "3600", "upsert": "true"},
    )
    return sb.storage.from_(BUCKET).get_public_url(path)


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


def ensure_bucket() -> None:
    """Create the public ``post-images`` bucket if absent. Idempotent one-time setup."""
    sb = get_supabase()
    names = {b.name for b in sb.storage.list_buckets()}
    if BUCKET not in names:
        sb.storage.create_bucket(BUCKET, options={"public": True})
