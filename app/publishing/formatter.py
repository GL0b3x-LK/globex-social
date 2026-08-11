"""Per-platform caption formatting. Same content everywhere; only formatting +
length limits differ. Hashtags sit in their own block; platforms differ mainly
in their hard character limits, which we trim to."""

from __future__ import annotations

import re

from app.logging_config import get_logger
from app.publishing.platforms import Platform

log = get_logger("app.publishing.formatter")

# Native caption character limits.
_LIMITS: dict[Platform, int] = {
    Platform.instagram: 2200,
    Platform.facebook: 63206,
    Platform.linkedin: 3000,
}

# Hashtags the publisher accepts in one post. Instagram itself allows 30, but
# Blotato rejects more than 5 with a 422 — and a rejected publish leaves the post
# `approved` forever, retrying into the same error. Enforce it here instead.
_MAX_HASHTAGS: dict[Platform, int] = {Platform.instagram: 5}

_HASHTAG = re.compile(r"#\w+")


def format_caption(caption: str, hashtags: list[str], platform: Platform) -> str:
    """Compose the post text for a platform, then trim to its limit.

    Hashtags already written into the caption are not appended a second time: a
    caption dictated with its tags on the end plus a populated hashtags field
    produced ten hashtags and a hard publish failure. Only the appended list is
    capped — hashtags a person wrote into the caption are their words, and are
    left alone rather than silently edited.
    """
    body = (caption or "").strip()
    inline = {t.lower() for t in _HASHTAG.findall(body)}
    wanted = [h.strip() for h in (hashtags or []) if h.strip()]

    fresh: list[str] = []
    seen = set(inline)
    for tag in wanted:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        fresh.append(tag)

    cap = _MAX_HASHTAGS.get(platform)
    if cap is not None:
        fresh = fresh[: max(0, cap - len(inline))]
        if len(inline) + len(fresh) > cap:
            log.warning(
                "caption carries more hashtags than the publisher accepts",
                extra={"platform": platform.value, "inline": len(inline), "limit": cap},
            )

    text = f"{body}\n\n{' '.join(fresh)}".strip() if fresh else body
    return trim_for_platform(text, platform)


def trim_for_platform(text: str, platform: Platform) -> str:
    limit = _LIMITS[platform]
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
