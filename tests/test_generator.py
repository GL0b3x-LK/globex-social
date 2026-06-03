"""Live content-generation tests (gated by RUN_AI_LIVE). Real Claude calls.

Run:  RUN_AI_LIVE=1 .venv\\Scripts\\python.exe -m pytest tests/test_generator.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.brand_check import brand_violations
from app.ai.generator import ContentCategory, generate_post

pytestmark = pytest.mark.usefixtures("anthropic_live")

# One representative (context, user_message) per category.
CASES: dict[ContentCategory, tuple[dict, str | None]] = {
    ContentCategory.trade_show: (
        {"show": "Gulfood", "dates": "2027-03-15 to 2027-03-19", "location": "Dubai", "variant": "pre"},
        "post about us heading to Gulfood",
    ),
    ContentCategory.holiday: (
        {"name": "National Poultry Day", "category": "food_industry", "date": "2027-03-19"},
        None,
    ),
    ContentCategory.milestone: (
        {"name": "Len Kogan", "title": "President", "years": 33},
        None,
    ),
    ContentCategory.founding_anniversary: (
        {"founded": "1993-11-05", "current_year": 2026, "years": 33},
        None,
    ),
    ContentCategory.stats: (
        {"figure": "150 ships on the water", "countries": "90+"},
        "post about us hitting 150 ships on the water",
    ),
    ContentCategory.announcement: (
        {"announcement": "new partnership with a major Southeast Asian seafood supplier"},
        None,
    ),
    ContentCategory.product_spotlight: (
        {"product": "duck", "angle": "premium duck products at global supply scale"},
        None,
    ),
    ContentCategory.promotional: (
        {"promo": "new branded packaging rollout across product lines"},
        None,
    ),
    ContentCategory.branded_packaging: (
        {"slot_number": 1, "caption_template": "Consistent branding, every box. Globex packaging built for the world's food trade."},
        None,
    ),
    ContentCategory.custom: (
        {"note": "Karen sent a photo of the team at the warehouse"},
        "something nice about our operations team",
    ),
}


@pytest.mark.parametrize(
    "category,context,message",
    [(c, ctx, msg) for c, (ctx, msg) in CASES.items()],
    ids=[c.value for c in CASES],
)
async def test_generate_each_category(category, context, message):
    post = await generate_post(category, context, message)
    assert post.caption.strip(), "empty caption"
    assert 1 <= len(post.hashtags) <= 8, f"hashtag count {len(post.hashtags)}"
    assert all(h.startswith("#") for h in post.hashtags), post.hashtags
    assert post.template_variant.strip(), "empty template_variant"
    violations = brand_violations(post.caption, post.hashtags)
    assert violations == [], f"brand violations {violations} in: {post.caption!r}"


async def test_vision_uses_photo_context_without_hallucinating():
    photo = next(Path("tests/fixtures/photos").glob("*.jpg"), None)
    assert photo is not None, "no sample photo fixture found"
    image_bytes = photo.read_bytes()
    post = await generate_post(
        ContentCategory.trade_show,
        {"show": "SIAL Paris", "variant": "during"},
        "post about us at SIAL Paris",
        image_bytes=image_bytes,
        image_media_type="image/jpeg",
    )
    assert "sial" in (post.caption + " " + post.rationale).lower(), post.caption
    assert brand_violations(post.caption, post.hashtags) == [], post.caption
