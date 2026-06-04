"""Platform-targeting parser tests ('post this only to LinkedIn')."""

from __future__ import annotations

from app.publishing import platforms as plat
from app.publishing.platforms import Platform


def test_only_linkedin() -> None:
    assert plat.parse_platforms("post this only to LinkedIn") == [Platform.linkedin]


def test_specific_subset_in_canonical_order() -> None:
    result = plat.parse_platforms("just FB and insta please")
    assert result == [Platform.instagram, Platform.facebook]  # canonical order, deduped


def test_all_keywords() -> None:
    assert plat.parse_platforms("post everywhere") == list(plat.ALL)
    assert plat.parse_platforms("send to all platforms") == list(plat.ALL)


def test_none_when_unmentioned() -> None:
    assert plat.parse_platforms("post about our duck line") is None
    assert plat.parse_platforms("") is None
    assert plat.parse_platforms(None) is None


def test_synonyms_and_casing() -> None:
    assert plat.parse_platforms("INSTAGRAM only") == [Platform.instagram]
    assert plat.parse_platforms("put it on linked in") == [Platform.linkedin]
    assert plat.parse_platforms("ig please") == [Platform.instagram]


def test_no_false_positive_inside_words() -> None:
    assert plat.parse_platforms("literally just post this") is None  # 'li' not matched


def test_normalize_defaults_to_all() -> None:
    assert plat.normalize(None) == list(plat.ALL)
    assert plat.normalize([]) == list(plat.ALL)
    assert plat.normalize(["linkedin"]) == [Platform.linkedin]
    assert plat.normalize(["bogus"]) == list(plat.ALL)  # unknown → safe default


def test_label() -> None:
    assert plat.label([Platform.linkedin]) == "LinkedIn"
    assert "all platforms" in plat.label(list(plat.ALL))
