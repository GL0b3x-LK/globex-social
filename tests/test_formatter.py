"""Per-platform caption formatting + length trimming."""

from __future__ import annotations

from app.publishing.formatter import format_caption, trim_for_platform
from app.publishing.platforms import Platform


def test_appends_hashtags_in_their_own_block() -> None:
    out = format_caption("Hello world", ["#Globex", "#Trade"], Platform.instagram)
    assert out.startswith("Hello world")
    assert "#Globex #Trade" in out


def test_no_hashtags_is_just_the_caption() -> None:
    assert format_caption("Just text", [], Platform.linkedin) == "Just text"


def test_trim_respects_instagram_limit() -> None:
    out = trim_for_platform("x" * 3000, Platform.instagram)  # IG = 2200
    assert len(out) <= 2200
    assert out.endswith("…")


def test_text_within_limit_is_untouched() -> None:
    text = "y" * 3000  # LinkedIn = 3000 exactly
    assert trim_for_platform(text, Platform.linkedin) == text
