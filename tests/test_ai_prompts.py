"""Always-on, deterministic AI tests (no API calls): prompt composition + brand lint."""
from __future__ import annotations

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
        rationale="Lead with the number.",
    )
    assert post.template_variant == "stats"
    assert post.hashtags == ["#GlobexInternational"]


def test_format_context():
    assert "Gulfood" in format_context({"show": "Gulfood", "location": "Dubai"})
    assert format_context(None)  # non-empty fallback string
