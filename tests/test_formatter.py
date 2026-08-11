"""Per-platform caption formatting + length trimming."""

from __future__ import annotations

import re

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


# --------------------------------------------------------------------------- #
# hashtags: never doubled, never over the publisher's ceiling
# --------------------------------------------------------------------------- #


def test_hashtags_already_in_the_caption_are_not_appended_twice() -> None:
    """The failure this prevents: a caption dictated with its tags on the end,
    plus a populated hashtags field, composed to ten hashtags — and Blotato
    rejected the publish outright (422, max 5)."""
    caption = "Retail-ready duck. #Globex #PremiumDuck #RetailReady #Packaging #GlobalFoodTrade"
    tags = ["#Globex", "#PremiumDuck", "#RetailReady", "#Packaging", "#GlobalFoodTrade"]

    out = format_caption(caption, tags, Platform.instagram)

    assert out == caption  # nothing appended
    assert len(re.findall(r"#\w+", out)) == 5


def test_instagram_gets_at_most_five_hashtags() -> None:
    out = format_caption("Body copy.", [f"#Tag{i}" for i in range(9)], Platform.instagram)
    assert len(re.findall(r"#\w+", out)) == 5


def test_the_cap_counts_hashtags_the_caption_already_has() -> None:
    out = format_caption(
        "Body #One #Two.", ["#Three", "#Four", "#Five", "#Six"], Platform.instagram
    )
    assert len(re.findall(r"#\w+", out)) == 5
    assert "#Six" not in out


def test_other_platforms_keep_every_hashtag() -> None:
    tags = [f"#Tag{i}" for i in range(9)]
    for platform in (Platform.facebook, Platform.linkedin):
        assert len(re.findall(r"#\w+", format_caption("Body.", tags, platform))) == 9


def test_a_duplicate_differing_only_in_case_is_still_a_duplicate() -> None:
    out = format_caption("Body #globex", ["#Globex", "#Duck"], Platform.instagram)
    assert len(re.findall(r"#\w+", out)) == 2
    assert "#Duck" in out
