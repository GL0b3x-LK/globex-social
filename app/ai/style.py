"""Capitalisation, enforced mechanically rather than requested in a prompt.

The client asked for four things, all of them the same thing: names should look
the same every time they appear.

* titles capitalised consistently — "Thank You, Americas Expo 2026";
* product names capitalised — "Whole Chicken", "Chicken Breast";
* countries and regions capitalised — "Latin American", "LATAM", "United States";
* show posts in that same style — "SIAL Shanghai 2026 – Day 2", not "Sial …".

A prompt asking for this would be obeyed most of the time, and "most of the
time" is exactly the problem: inconsistency IS the defect, so a rule that holds
in four captions out of five has not fixed anything. The canonical spellings are
therefore applied to the finished post in code.

Two deliberate limits:

* a field that is entirely upper case is left in upper case. On-image titles are
  ALL CAPS by standing rule and by ``text-transform`` in the templates, and an
  operator who asked for caps has not asked for Title Case. Upper case is
  already internally consistent, which is what was asked for.
* only the HEADLINE is title-cased. Subheads and captions run in sentence case
  in every approved reference ("21–24 April · Singapore, Singapore"), so they
  get proper-noun correction and nothing else.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.logging_config import get_logger

log = get_logger("app.ai.style")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Words that stay lower case inside a title unless they open or close it, or
# follow a dash/colon that starts a new segment ("… – Day 2").
_SMALL_WORDS = frozenset(
    """a an the and but or nor for so yet at by in of on to up via vs with from
    into onto over under as per off out""".split()
)

# Regions, countries and cities Globex actually trades into or exhibits in.
# Written the way the client writes them.
_PLACES = [
    # regions / blocs
    "Latin America",
    "Latin American",
    "LATAM",
    "South America",
    "Central America",
    "North America",
    "Caribbean",
    "Middle East",
    "GCC",
    "Southeast Asia",
    "South East Asia",
    "East Asia",
    "Central Asia",
    "Far East",
    "West Africa",
    "East Africa",
    "North Africa",
    "Sub-Saharan Africa",
    "Eastern Europe",
    "Western Europe",
    "European Union",
    "Asia Pacific",
    "Asia",
    "Africa",
    "Europe",
    "Oceania",
    # countries
    "United States",
    "United Kingdom",
    "United Arab Emirates",
    "Saudi Arabia",
    "South Korea",
    "South Africa",
    "Dominican Republic",
    "Ivory Coast",
    "Côte d'Ivoire",
    "New Zealand",
    "Sierra Leone",
    "Costa Rica",
    "El Salvador",
    "Trinidad and Tobago",
    "Papua New Guinea",
    "China",
    "Japan",
    "Vietnam",
    "Singapore",
    "Malaysia",
    "Indonesia",
    "Philippines",
    "Thailand",
    "India",
    "Pakistan",
    "Bangladesh",
    "Cambodia",
    "Myanmar",
    "Taiwan",
    "Mexico",
    "Brazil",
    "Colombia",
    "Peru",
    "Chile",
    "Argentina",
    "Ecuador",
    "Guatemala",
    "Honduras",
    "Nicaragua",
    "Panama",
    "Venezuela",
    "Bolivia",
    "Paraguay",
    "Uruguay",
    "Cuba",
    "Haiti",
    "Jamaica",
    "Canada",
    "Australia",
    "Angola",
    "Ghana",
    "Nigeria",
    "Kenya",
    "Egypt",
    "Morocco",
    "Algeria",
    "Libya",
    "Tunisia",
    "Benin",
    "Togo",
    "Senegal",
    "Cameroon",
    "Gabon",
    "Mozambique",
    "Liberia",
    "Congo",
    "Tanzania",
    "Uganda",
    "Zambia",
    "Zimbabwe",
    "Namibia",
    "Mauritania",
    "Gambia",
    "Guinea",
    "Ukraine",
    "Poland",
    "Netherlands",
    "Germany",
    "Spain",
    "France",
    "Italy",
    "Portugal",
    "Belgium",
    "Ireland",
    "Denmark",
    "Sweden",
    "Norway",
    "Turkey",
    "Greece",
    "Romania",
    "Qatar",
    "Kuwait",
    "Oman",
    "Bahrain",
    "Jordan",
    "Lebanon",
    "Iraq",
    "Yemen",
    "Israel",
    # cities that show up in show datelines
    "New York",
    "Atlanta",
    "Miami",
    "Los Angeles",
    "Chicago",
    "Houston",
    "Dubai",
    "Abu Dhabi",
    "Shanghai",
    "Beijing",
    "Guangzhou",
    "Hong Kong",
    "Manila",
    "Ho Chi Minh City",
    "Bangkok",
    "Jakarta",
    "Seoul",
    "Tokyo",
    "Bogotá",
    "Bogota",
    "Barranquilla",
    "Cartagena",
    "Lima",
    "Santiago",
    "São Paulo",
    "Sao Paulo",
    "Panama City",
    "Paris",
    "Cologne",
    "Barcelona",
    "Madrid",
    "Rotterdam",
    "Hamburg",
    "London",
    "Lisbon",
    "Riyadh",
    "Jeddah",
    "Cairo",
    "Lagos",
    "Accra",
    "Luanda",
    "Abidjan",
    "Dakar",
]

# Trade shows and industry bodies. Acronyms carry their own house casing — the
# client called this out by name ("SIAL Shanghai 2026", not "Sial Shanghai 2026").
_SHOWS = [
    "SIAL",
    "SIAL Paris",
    "SIAL Shanghai",
    "SIAL China",
    "IPPE",
    "NPFDA",
    "IPPE/NPFDA",
    "FHA",
    "FHV",
    "WOFEX",
    "USAPEEC",
    "Anuga",
    "Gulfood",
    "Alimentec",
    "Food & Hospitality Asia",
    "Food & Hospitality Vietnam",
    "World Food Expo",
    "Seafood Expo Global",
    "Americas Food & Beverage",
    "Americas Food and Beverage",
    "Americas Expo",
    "Expo Alimentaria",
]

# Company and trade vocabulary that reads as a name, not a description.
_TRADE_TERMS = [
    "Globex",
    "Globex International",
    # "Quality Control" is capitalised on purpose — it is the client's approved
    # replacement for the struck "inspected by hand", so it reads as a named
    # process. Generic trade vocabulary ("cold chain", "full container load")
    # deliberately stays lower case: capitalising it reads as a brand name.
    "Quality Control",
    "HACCP",
    "USDA",
    "FDA",
    "FSIS",
    "ISO",
    "Ramadan",
    "Eid",
    "Chinese New Year",
    "Lunar New Year",
    "Thanksgiving",
    "Memorial Day",
    "Independence Day",
    "Labor Day",
    "Super Bowl",
    "Easter",
    "Christmas",
]


def _product_names() -> list[str]:
    """Product names exactly as the product catalogue spells them.

    The catalogue is already canonical ("Chicken Leg Quarters", "Whole Chicken"),
    so it is the source of truth rather than a list retyped here and left to rot.
    """
    try:
        raw = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a missing catalogue must not break generation
        log.error("could not read product catalogue", extra={"error": str(exc)[:120]})
        return []
    items = raw if isinstance(raw, list) else raw.get("products", [])
    names: list[str] = []
    for item in items:
        name = (item or {}).get("name") or ""
        # "Whole Chicken (Griller)" contributes both the full name and the base.
        base = re.sub(r"\s*\([^)]*\)", "", name).strip()
        for candidate in (name, base):
            # Single common words ("Beef", "Turkey") are left alone: capitalising
            # every "beef" mid-sentence reads as a brand name, not a product.
            if candidate and len(candidate.split()) > 1:
                names.append(candidate)
    return names


@lru_cache(maxsize=1)
def canonical_terms() -> dict[str, str]:
    """Lower-cased term -> the spelling it should always appear in."""
    terms: dict[str, str] = {}
    for group in (_product_names(), _PLACES, _SHOWS, _TRADE_TERMS):
        for term in group:
            terms.setdefault(term.lower(), term)
    return terms


@lru_cache(maxsize=1)
def _term_pattern() -> re.Pattern[str]:
    """One alternation, longest first so "Latin America" wins over "America".

    The trailing guard is ``(?!\\w)``, which stops "Asia" firing inside "Asian"
    while still catching the possessive "Asia's" — a name is a name whether or
    not it owns something.
    """
    terms = sorted(canonical_terms(), key=len, reverse=True)
    return re.compile(r"(?<![\w'])(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.I)


# "SIAL Shanghai 2026 – Day 2" is the client's own example of show formatting,
# and the day label reads as part of the name wherever it lands.
_DAY_LABEL = re.compile(r"\b(day)(\s+\d+)", re.I)


def is_shouted(text: str) -> bool:
    """True when the text is deliberately upper case (so its case is not ours to change)."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def fix_terms(text: str) -> str:
    """Respell known names — products, places, shows — in their canonical form."""
    if not text or is_shouted(text):
        return text
    terms = canonical_terms()
    text = _term_pattern().sub(lambda m: terms[m.group(0).lower()], text)
    return _DAY_LABEL.sub(lambda m: m.group(1).capitalize() + m.group(2), text)


def _cap_word(word: str) -> str:
    """Capitalise one word, leaving acronyms and hyphenated parts intact."""
    if not word:
        return word
    if len(word) > 1 and is_shouted(word):
        return word  # SIAL, LATAM, USDA — already correct
    if "-" in word:
        return "-".join(_cap_word(part) for part in word.split("-"))
    return word[:1].upper() + word[1:]


def title_case(text: str) -> str:
    """Title Case, the way the client writes titles: "Thank You, Americas Expo 2026".

    Small words stay lower case in the middle of a title, but a word following a
    dash or colon opens a new segment and is capitalised — which is what makes
    "SIAL Shanghai 2026 – Day 2" read right.
    """
    if not text or is_shouted(text):
        return text
    tokens = re.split(r"(\s+)", text)
    word_positions = [i for i, t in enumerate(tokens) if t.strip()]
    last = word_positions[-1] if word_positions else -1

    out: list[str] = []
    opens_segment = True
    for i, token in enumerate(tokens):
        if not token.strip():
            out.append(token)
            continue
        core = token.strip(",.;:!?\"'()")
        if opens_segment or i == last or core.lower() not in _SMALL_WORDS:
            out.append(_cap_word(token))
        else:
            out.append(token[:1].lower() + token[1:] if token[:1].isalpha() else token)
        # A dash, colon or full stop ends a segment, so the next word opens a new
        # one. Without the sentence marks, "Truck to Container. On time." came
        # back as "… on Time." — "on" treated as mid-title rather than opening.
        opens_segment = bool(re.search(r"[—–\-:|.!?]$", token))
    return "".join(out)


def enforce(post: object, *, feedback: str = "") -> object:
    """Apply the client's capitalisation to a GeneratedPost, in place.

    ``feedback`` is the operator's edit instruction, when there is one. If they
    said anything about case, their instruction wins outright and the headline is
    left exactly as the editor produced it — house style losing to an explicit
    request is the whole point of the edit prompt, and re-imposing it here would
    hand back the same bug through the back door.
    """
    caption = getattr(post, "caption", None)
    headline = getattr(post, "headline", None)
    subhead = getattr(post, "subhead", None)

    if caption:
        post.caption = fix_terms(caption)  # type: ignore[attr-defined]
    if subhead:
        post.subhead = fix_terms(subhead)  # type: ignore[attr-defined]
    if headline:
        fixed = fix_terms(headline)
        if not _mentions_case(feedback):
            fixed = fix_terms(title_case(fixed))
        post.headline = fixed  # type: ignore[attr-defined]
    return post


_CASE_WORDS = ("caps", "capital", "uppercase", "upper case", "lowercase", "lower case", "case")


def _mentions_case(feedback: str) -> bool:
    return any(w in (feedback or "").lower() for w in _CASE_WORDS)
