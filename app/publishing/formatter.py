"""Per-platform caption formatting. Same content everywhere; only formatting +
length limits differ. Hashtags sit in their own block; platforms differ mainly
in their hard character limits, which we trim to."""

from __future__ import annotations

from app.publishing.platforms import Platform

# Native caption character limits.
_LIMITS: dict[Platform, int] = {
    Platform.instagram: 2200,
    Platform.facebook: 63206,
    Platform.linkedin: 3000,
}


def format_caption(caption: str, hashtags: list[str], platform: Platform) -> str:
    """Compose the post text for a platform, then trim to its limit."""
    body = (caption or "").strip()
    tags = " ".join(h.strip() for h in (hashtags or []) if h.strip())
    text = f"{body}\n\n{tags}".strip() if tags else body
    return trim_for_platform(text, platform)


def trim_for_platform(text: str, platform: Platform) -> str:
    limit = _LIMITS[platform]
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
