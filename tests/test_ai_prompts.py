"""Always-on, deterministic AI tests (no API calls): prompt composition + brand lint."""

from __future__ import annotations

from app.ai import generator
from app.ai.brand_check import brand_violations, emoji_count
from app.ai.generator import ContentCategory, GeneratedPost, format_context, system_for
from app.ai.prompts.brand import BRAND_BLOCK


def test_every_category_composes_a_full_system_prompt():
    for category in ContentCategory:
        system = system_for(category)
        assert BRAND_BLOCK in system, f"{category} missing brand block"
        # category-specific text is appended after the brand block
        assert len(system) > len(BRAND_BLOCK) + 50, f"{category} has no category prompt"


def test_brand_block_states_the_hard_donts():
    block = BRAND_BLOCK.lower()
    for term in ["birthday", "recipe", "news", "kitsch", "hashtag", "pantone"]:
        assert term in block, f"brand block should mention {term!r}"


def test_brand_violations_flags_each_donts():
    assert "birthday" in brand_violations("Happy birthday to our amazing team!")
    assert "recipe" in brand_violations("Try this recipe: 2 cups flour, preheat the oven")
    assert "news_reference" in brand_violations("Breaking news from the food industry today")
    assert "emoji_spam" in brand_violations("What a day 🎉🎊🥳🎈")
    assert "hashtag_stuffing" in brand_violations("ok", ["#x"] * 9)


def test_brand_violations_passes_clean_copy():
    caption = "150 ships on the water right now. 90+ countries served."
    assert brand_violations(caption, ["#GlobexInternational", "#GlobalFoodTrade"]) == []


def test_emoji_count():
    assert emoji_count("no emoji here") == 0
    assert emoji_count("one 🚢 ship") == 1


def test_generated_post_model_shape():
    post = GeneratedPost(
        caption="150 ships on the water.",
        hashtags=["#GlobexInternational"],
        template_variant="stats",
        headline="150 on the water",
        figure="150",
        rationale="Lead with the number.",
    )
    assert post.template_variant == "stats"
    assert post.hashtags == ["#GlobexInternational"]
    assert post.figure == "150"
    assert post.subhead is None  # optional display field defaults to None


def test_format_context():
    assert "Gulfood" in format_context({"show": "Gulfood", "location": "Dubai"})
    assert format_context(None)  # non-empty fallback string


# --------------------------------------------------------------------------- #
# the client's no-say list, enforced on posts as well as video
# --------------------------------------------------------------------------- #


def _post(**over) -> generator.GeneratedPost:
    base = dict(
        caption="Whole chicken, export grade. Shipped globally.",
        hashtags=["#GlobexInternational"],
        template_variant="TS-p3-editorial_4x5",
        headline="Whole Chicken",
        rationale="r",
    )
    base.update(over)
    return generator.GeneratedPost(**base)


def test_a_clean_post_has_no_banned_claims() -> None:
    assert generator.banned_claims(_post()) == []


def test_halal_in_a_caption_is_caught() -> None:
    """The first scheduled post volunteered 'halal on request' with no prompt for it."""
    assert "Halal" in generator.banned_claims(_post(caption="IQF or bulk-pack, halal on request."))


def test_country_count_is_caught_wherever_it_appears() -> None:
    """Caption, on-image headline and subhead all reach the reader."""
    assert generator.banned_claims(_post(caption="Delivery into 90+ countries."))
    assert generator.banned_claims(_post(headline="90+ countries served"))
    assert generator.banned_claims(_post(subhead="Trusted in 90+ countries"))


def test_hand_inspection_is_caught() -> None:
    assert generator.banned_claims(_post(caption="Every bird inspected by hand."))


def test_no_prompt_asks_for_a_struck_phrase() -> None:
    """The prompts used to instruct the model to say '90+ countries' outright,
    which is the client's own struck phrase."""
    from app.ai.generator import ContentCategory, system_for

    for category in ContentCategory:
        text = system_for(category).lower()
        assert "90+ countries" not in text, category
        assert "inspected by hand" not in text, category
