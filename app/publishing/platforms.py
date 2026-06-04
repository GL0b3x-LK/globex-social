"""Social platforms + a deterministic parser for "post this only to LinkedIn".

Naming a platform is treated as exclusive — "post to LinkedIn" means LinkedIn
only. "all"/"everywhere" resets to every platform. No platform mentioned returns
None, so the caller keeps whatever the post already targets (default: all).
"""

from __future__ import annotations

import re
from enum import StrEnum


class Platform(StrEnum):
    instagram = "instagram"
    facebook = "facebook"
    linkedin = "linkedin"


ALL: tuple[Platform, ...] = (Platform.instagram, Platform.facebook, Platform.linkedin)

# Word-boundary synonyms per platform. "li" is intentionally excluded (too many
# false positives); "ig"/"fb"/"insta" are safe with \b.
_PATTERNS: dict[Platform, re.Pattern[str]] = {
    Platform.instagram: re.compile(r"\b(instagram|insta|ig)\b", re.I),
    Platform.facebook: re.compile(r"\b(facebook|fb)\b", re.I),
    Platform.linkedin: re.compile(r"\blinked[\s-]?in\b", re.I),
}

_ALL_RE = re.compile(
    r"\b(everywhere|all (platforms|socials|channels|three|of them)|to all)\b", re.I
)


def parse_platforms(text: str | None) -> list[Platform] | None:
    """Platforms named in `text`, or None if none are mentioned.

    'all'/'everywhere' → every platform; specific names → exactly those.
    """
    if not text:
        return None
    if _ALL_RE.search(text):
        return list(ALL)
    found = [p for p in ALL if _PATTERNS[p].search(text)]  # canonical order, deduped
    return found or None


def normalize(values: list[str] | None) -> list[Platform]:
    """A stored target_platforms list (or None) → the effective Platform list (default all)."""
    if not values:
        return list(ALL)
    out = [p for p in ALL if p.value in set(values)]
    return out or list(ALL)


def label(platforms: list[Platform]) -> str:
    """Human label for a preview/status line, e.g. 'Instagram · LinkedIn'."""
    names = {
        Platform.instagram: "Instagram",
        Platform.facebook: "Facebook",
        Platform.linkedin: "LinkedIn",
    }
    if set(platforms) == set(ALL):
        return "all platforms (Instagram · Facebook · LinkedIn)"
    return " · ".join(names[p] for p in platforms)
