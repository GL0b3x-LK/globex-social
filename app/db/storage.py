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


def _upload_sync(path: str, png_bytes: bytes) -> str:
    sb = get_supabase()
    sb.storage.from_(BUCKET).upload(
        path,
        png_bytes,
        {"content-type": "image/png", "cache-control": "3600", "upsert": "true"},
    )
    return sb.storage.from_(BUCKET).get_public_url(path)


async def upload_png(post_id: str | UUID, png_bytes: bytes, *, suffix: str = "") -> str:
    """Upload a rendered PNG to ``post-images/{post_id}{suffix}.png``; return its public URL.

    ``upsert`` is on so re-rendering after an edit overwrites the same object. ``suffix``
    distinguishes sibling objects for one post — e.g. ``suffix="-raw"`` hosts the raw
    AI-generated image (kept so img2img edits can transform it) alongside the composite.
    """
    return await asyncio.to_thread(_upload_sync, f"{post_id}{suffix}.png", png_bytes)


def ensure_bucket() -> None:
    """Create the public ``post-images`` bucket if absent. Idempotent one-time setup."""
    sb = get_supabase()
    names = {b.name for b in sb.storage.list_buckets()}
    if BUCKET not in names:
        sb.storage.create_bucket(BUCKET, options={"public": True})
