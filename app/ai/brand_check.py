"""Deterministic brand-voice lint for generated captions.

Catches the hard DON'Ts from the brand rules (birthdays, recipes, news, emoji
spam, hashtag stuffing). Used to assert on generated output in tests and as a
guardrail in later phases.
"""
from __future__ import annotations

import re

_BIRTHDAY = re.compile(r"\b(birthday|b-?day)\b|🎂|🎈|🥳", re.IGNORECASE)
_RECIPE = re.compile(
    r"\b(recipe|ingredients?|preheat|tablespoons?|teaspoons?|marinate|simmer)\b|\b\d+\s?cups?\b",
    re.IGNORECASE,
)
_NEWS = re.compile(
    r"\b(breaking news|in the news|headlines?|news report|according to reports)\b",
    re.IGNORECASE,
)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\U00002b00-\U00002bff\U0000fe00-\U0000fe0f\U00002190-\U000021ff]"
)

MAX_EMOJI = 2
MAX_HASHTAGS = 8


def emoji_count(text: str) -> int:
    return len(_EMOJI.findall(text))


def brand_violations(caption: str, hashtags: list[str] | None = None) -> list[str]:
    """Return a list of brand-rule violation tags. Empty list == clean."""
    issues: list[str] = []
    if _BIRTHDAY.search(caption):
        issues.append("birthday")
    if _RECIPE.search(caption):
        issues.append("recipe")
    if _NEWS.search(caption):
        issues.append("news_reference")
    if emoji_count(caption) > MAX_EMOJI:
        issues.append("emoji_spam")
    if hashtags is not None and len(hashtags) > MAX_HASHTAGS:
        issues.append("hashtag_stuffing")
    return issues
