"""The client's capitalisation rules, enforced rather than requested.

"Titles should use capitalization consistently, such as Thank You, Americas
Expo 2026. Product names should be capitalized... Countries and regions should
also be consistently capitalized... Show posts should follow the same formatting
style. For example, SIAL Shanghai 2026 – Day 2 rather than Sial Shanghai 2026."

Inconsistency IS the defect here, so these are checks, not prompt requests: a
rule obeyed four captions in five has not fixed anything.
"""

from __future__ import annotations

import pytest

from app.ai import style
from app.ai.generator import GeneratedPost


def _post(**over) -> GeneratedPost:
    base = dict(
        caption="Caption text.",
        hashtags=["#GlobexInternational"],
        template_variant="ts_p1_bolddip",
        headline="Headline",
        rationale="test",
    )
    base.update(over)
    return GeneratedPost(**base)


# --------------------------------------------------------------------------- #
# titles
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Thank you, americas expo 2026", "Thank You, Americas Expo 2026"),
        ("thank you, SIAL Paris", "Thank You, SIAL Paris"),
        ("meet us at gulfood 2027", "Meet Us at Gulfood 2027"),
        ("inside the cold store", "Inside the Cold Store"),
        ("further-processed chicken", "Further-Processed Chicken"),
        ("griller vs roaster", "Griller vs Roaster"),
    ],
)
def test_titles_are_title_cased_the_way_the_client_writes_them(raw: str, expected: str) -> None:
    assert style.title_case(style.fix_terms(raw)) == expected


def test_small_words_stay_down_in_the_middle_but_never_at_the_edges() -> None:
    assert style.title_case("the paperwork that travels to") == "The Paperwork That Travels To"
    assert style.title_case("line to freezer in minutes") == "Line to Freezer in Minutes"


def test_a_word_after_a_dash_opens_a_new_segment() -> None:
    """The client's own example: "SIAL Shanghai 2026 – Day 2", not "– day 2"."""
    assert style.title_case("SIAL Shanghai 2026 – day 2") == "SIAL Shanghai 2026 – Day 2"
    assert style.title_case("save the date — sial shanghai") == "Save the Date — Sial Shanghai"


def test_an_all_caps_title_is_left_alone() -> None:
    """On-image titles are ALL CAPS by standing rule and by the templates'
    text-transform. Upper case is already consistent — Title Case is not owed."""
    assert style.title_case("DUCK BUILT FOR ASIAN SPECS") == "DUCK BUILT FOR ASIAN SPECS"


# --------------------------------------------------------------------------- #
# products, places, shows
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("we ship whole chicken weekly", "we ship Whole Chicken weekly"),
        ("chicken breast and chicken leg quarters", "Chicken Breast and Chicken Leg Quarters"),
        ("duck retail pack for retail", "Duck Retail Pack for retail"),
    ],
)
def test_product_names_are_capitalised(raw: str, expected: str) -> None:
    assert style.fix_terms(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("shipping into latam every week", "shipping into LATAM every week"),
        ("our latin american partners", "our Latin American partners"),
        ("buyers in the united states", "buyers in the United States"),
        ("across southeast asia and the middle east", "across Southeast Asia and the Middle East"),
    ],
)
def test_countries_and_regions_are_capitalised(raw: str, expected: str) -> None:
    assert style.fix_terms(raw) == expected


def test_the_longest_matching_place_wins() -> None:
    """ "Latin America" must not be half-matched as "America"."""
    assert style.fix_terms("our latin america desk") == "our Latin America desk"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sial shanghai 2026", "SIAL Shanghai 2026"),
        ("live at sial paris", "live at SIAL Paris"),
        ("meet us at ippe/npfda", "meet us at IPPE/NPFDA"),
        ("usapeec americas expo", "USAPEEC Americas Expo"),
        ("wofex world food expo", "WOFEX World Food Expo"),
        ("anuga and gulfood", "Anuga and Gulfood"),
    ],
)
def test_show_names_keep_their_house_spelling(raw: str, expected: str) -> None:
    assert style.fix_terms(raw) == expected


def test_a_term_inside_a_longer_word_is_not_touched() -> None:
    """ "Asia" must not fire inside "Asian"; the term list carries "Asian" forms
    it wants ("Latin American") explicitly."""
    assert style.fix_terms("asiatic route") == "asiatic route"
    assert style.fix_terms("chinatown") == "chinatown"


# --------------------------------------------------------------------------- #
# where it applies, and where the operator outranks it
# --------------------------------------------------------------------------- #


def test_enforce_title_cases_the_headline_but_not_the_caption() -> None:
    """Every approved reference runs the subhead and caption in sentence case
    ("21–24 April · Singapore, Singapore") — only the headline is a title."""
    post = _post(
        headline="thank you, sial paris",
        subhead="three days on the floor in paris",
        caption="Thank you to every partner who stopped by in latam.",
    )
    style.enforce(post)
    assert post.headline == "Thank You, SIAL Paris"
    assert post.subhead == "three days on the floor in Paris"  # names fixed, case untouched
    assert post.caption == "Thank you to every partner who stopped by in LATAM."


def test_an_operator_who_spoke_about_case_outranks_house_style() -> None:
    """The day-one bug was the editor obeying house style over the operator.
    Re-imposing Title Case here would hand that straight back."""
    post = _post(headline="all lower case please")
    style.enforce(post, feedback="make the title all lower case")
    assert post.headline == "all lower case please"


def test_without_case_feedback_the_headline_is_still_normalised() -> None:
    post = _post(headline="duck for asia's tables")
    style.enforce(post, feedback="shorten the caption")
    assert post.headline == "Duck for Asia's Tables"


def test_enforce_leaves_an_all_caps_headline_shouting() -> None:
    post = _post(headline="GRILLER OR ROASTER")
    style.enforce(post)
    assert post.headline == "GRILLER OR ROASTER"


def test_products_come_from_the_catalogue_not_a_retyped_list() -> None:
    """The catalogue is already canonical, so it is the source of truth and
    cannot drift out of step with a copy kept here."""
    terms = style.canonical_terms()
    assert terms["chicken leg quarters"] == "Chicken Leg Quarters"
    assert terms["whole chicken"] == "Whole Chicken"  # from "Whole Chicken (Griller)"


def test_single_word_products_are_not_capitalised_mid_sentence() -> None:
    """Capitalising every "beef" reads as a brand name, not a product."""
    assert style.fix_terms("we move beef and lamb at scale") == "we move beef and lamb at scale"


def test_a_possessive_name_is_still_a_name() -> None:
    """ "Asia" must not fire inside "Asian", but "Asia's" is the same name."""
    assert style.fix_terms("duck for asia's tables") == "duck for Asia's tables"
    assert style.fix_terms("asian specs") == "asian specs"


def test_the_day_label_is_capitalised_wherever_it_lands() -> None:
    """The client's own show example — "SIAL Shanghai 2026 – Day 2" — reads the
    same whether it is the headline or the line under it."""
    assert style.fix_terms("sial shanghai 2026 – day 2") == "SIAL Shanghai 2026 – Day 2"
    assert style.fix_terms("live on day 3") == "live on Day 3"
    assert style.fix_terms("a good day for it") == "a good day for it"


def test_a_full_stop_opens_a_new_segment() -> None:
    """ "Truck to Container. On time." — a small word after a sentence mark opens
    the next segment and is capitalised, not treated as mid-title."""
    assert style.title_case("truck to container. on time.") == "Truck to Container. On Time."
    assert style.title_case("marinated. portioned. kitchen-ready.") == (
        "Marinated. Portioned. Kitchen-Ready."
    )


def test_generic_trade_vocabulary_stays_lower_case() -> None:
    """ "cold chain" is a description, not a name — capitalising it mid-caption
    reads as a brand. "Quality Control" is capitalised because it IS the
    client's approved named process."""
    assert style.fix_terms("our cold chain protects it") == "our cold chain protects it"
    assert style.fix_terms("quality control is a habit") == "Quality Control is a habit"
