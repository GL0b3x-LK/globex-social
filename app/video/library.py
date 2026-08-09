"""Character and product libraries — the video engine's nouns.

When the operator says "a video of John holding our raw chicken", something has to
turn "John" into a persona with a locked voice and "raw chicken" into a product
with real pack shots and a no-say list. That is this module.

Source of truth is app/data/characters.json + products.json (mirrored into the
Supabase tables in schema.sql once SUPABASE_DB_URL is configured — the JSON stays
the seed either way, exactly like employees/holidays/trade_shows).

Resolution is deliberately two-tier:

  * a **handle** match (name, full name, or an explicit alias, matched on word
    boundaries) is a confident hit — "duck" is an alias of the retail pack, so it
    resolves rather than asking;
  * failing that, a **token** match ("chicken" appears in six products) returns
    every plausible candidate so the caller can ask ONE clarifying question
    instead of guessing. Guessing the wrong SKU is worse than asking.

Nothing here talks to a generation provider; it is pure data + matching, so the
client's content rules (claims_forbidden, presenter matching) are enforceable and
testable long before any money is spent on a render.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CHARACTERS_PATH = DATA_DIR / "characters.json"
PRODUCTS_PATH = DATA_DIR / "products.json"
ASSET_POOL = DATA_DIR / "asset_pool"

# Words too generic to identify a product on their own — matching on these would
# make every poultry line a candidate for every brief.
_STOPWORD_TOKENS = frozenset(
    {
        "the",
        "our",
        "a",
        "an",
        "and",
        "of",
        "for",
        "pack",
        "packs",
        "carton",
        "cartons",
        "box",
        "boxes",
        "retail",
        "export",
        "bulk",
        "frozen",
        "raw",
        "globex",
        "branded",
        "product",
        "products",
    }
)


@dataclass(frozen=True)
class Character:
    slug: str
    name: str
    full_name: str
    aliases: tuple[str, ...]
    gender: str
    ethnicity: str
    age: int
    role: str
    persona: str
    speaking_style: str
    appearance_notes: str
    setting_affinity: tuple[str, ...]
    market_tags: tuple[str, ...]
    visual_prompt: str
    voice_direction: str
    voice_id: str | None
    reference_image_urls: tuple[str, ...]
    is_real_person: bool
    likeness_consent: dict[str, Any] | None
    status: str

    @property
    def usable(self) -> bool:
        """Approved, and (if a real person) covered by recorded likeness consent.

        Draft characters are deliberately unusable: Len approves each sheet once,
        and only then can that face front the brand.
        """
        if self.status != "approved":
            return False
        return not self.is_real_person or bool(self.likeness_consent)

    @property
    def handles(self) -> tuple[str, ...]:
        return (self.name, self.full_name, *self.aliases)


@dataclass(frozen=True)
class Product:
    slug: str
    name: str
    aliases: tuple[str, ...]
    category: str
    description: str
    formats: tuple[str, ...]
    pack_shot_files: tuple[str, ...]
    product_shot_files: tuple[str, ...]
    talking_points: tuple[str, ...]
    claims_forbidden: tuple[str, ...]
    visual_rules: dict[str, Any] = field(default_factory=dict)
    markets: tuple[str, ...] = ()
    presenter_preference: tuple[str, ...] = ()
    status: str = "active"

    @property
    def handles(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    @property
    def hero_files(self) -> tuple[str, ...]:
        """Images that may front a video, packaging first.

        Raw product shots are excluded for anything flagged ``never_raw_hero``
        (whole birds, offal, frames) — the client rejected graphic carcass
        imagery, so that rule lives in the data and is honoured here.
        """
        if self.visual_rules.get("never_raw_hero"):
            return self.pack_shot_files
        return (*self.pack_shot_files, *self.product_shot_files)

    def asset_paths(self, files: tuple[str, ...] | None = None) -> tuple[Path, ...]:
        """Absolute paths into the curated asset pool for the given files."""
        return tuple(ASSET_POOL / f for f in (self.hero_files if files is None else files))


def _tuple(value: Any) -> tuple[str, ...]:
    return tuple(value or ())


@lru_cache(maxsize=1)
def _characters_doc() -> dict[str, Any]:
    return json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def _products_doc() -> dict[str, Any]:
    return json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def load_characters() -> tuple[Character, ...]:
    return tuple(
        Character(
            slug=c["slug"],
            name=c["name"],
            full_name=c.get("full_name") or c["name"],
            aliases=_tuple(c.get("aliases")),
            gender=c.get("gender", ""),
            ethnicity=c.get("ethnicity", ""),
            age=int(c.get("age") or 0),
            role=c.get("role", ""),
            persona=c.get("persona", ""),
            speaking_style=c.get("speaking_style", ""),
            appearance_notes=c.get("appearance_notes", ""),
            setting_affinity=_tuple(c.get("setting_affinity")),
            market_tags=_tuple(c.get("market_tags")),
            visual_prompt=c.get("visual_prompt", ""),
            voice_direction=c.get("voice_direction", ""),
            voice_id=c.get("voice_id"),
            reference_image_urls=_tuple(c.get("reference_image_urls")),
            is_real_person=bool(c.get("is_real_person")),
            likeness_consent=c.get("likeness_consent"),
            status=c.get("status", "draft"),
        )
        for c in _characters_doc()["characters"]
    )


@lru_cache(maxsize=1)
def load_products() -> tuple[Product, ...]:
    return tuple(
        Product(
            slug=p["slug"],
            name=p["name"],
            aliases=_tuple(p.get("aliases")),
            category=p.get("category", ""),
            description=p.get("description", ""),
            formats=_tuple(p.get("formats")),
            pack_shot_files=_tuple(p.get("pack_shot_files")),
            product_shot_files=_tuple(p.get("product_shot_files")),
            talking_points=_tuple(p.get("talking_points")),
            claims_forbidden=_tuple(p.get("claims_forbidden")),
            visual_rules=dict(p.get("visual_rules") or {}),
            markets=_tuple(p.get("markets")),
            presenter_preference=_tuple(p.get("presenter_preference")),
            status=p.get("status", "active"),
        )
        for p in _products_doc()["products"]
    )


def global_claims_forbidden() -> tuple[str, ...]:
    """Client-mandated no-say terms that apply to every script."""
    return _tuple(_products_doc()["_meta"].get("global_claims_forbidden"))


def wardrobe_rule() -> str:
    return str(_characters_doc()["_meta"].get("wardrobe_rule", ""))


def generation_rules() -> tuple[str, ...]:
    return _tuple(_characters_doc()["_meta"].get("generation_rules"))


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #


def _contains(text: str, phrase: str) -> bool:
    """Whole-word/phrase containment — 'duck' must not match 'ducking'."""
    return re.search(rf"(?<!\w){re.escape(phrase.lower())}(?!\w)", text) is not None


def _tokens(*phrases: str) -> set[str]:
    out: set[str] = set()
    for phrase in phrases:
        for token in re.findall(r"[a-z]+", phrase.lower()):
            if len(token) > 2 and token not in _STOPWORD_TOKENS:
                out.add(token)
    return out


def _match(text: str, entities: list[Any]) -> list[Any]:
    """Handle matches first (confident); token matches only as a fallback.

    Returns every plausible entity, most specific first. One result means we can
    proceed; more than one means the caller should ask.
    """
    haystack = text.lower()

    scored = [
        (len(h), e)
        for e in entities
        for h in sorted(e.handles, key=len, reverse=True)
        if _contains(haystack, h)
    ]
    if scored:
        best: dict[str, tuple[int, Any]] = {}
        for score, entity in scored:
            if entity.slug not in best or score > best[entity.slug][0]:
                best[entity.slug] = (score, entity)
        ranked = sorted(best.values(), key=lambda pair: (-pair[0], pair[1].slug))
        return [entity for _, entity in ranked]

    said = _tokens(haystack)
    return sorted(
        (e for e in entities if _tokens(*e.handles) & said),
        key=lambda e: e.slug,
    )


def match_characters(text: str, *, usable_only: bool = False) -> list[Character]:
    pool = [c for c in load_characters() if c.usable] if usable_only else list(load_characters())
    return _match(text, [c for c in pool if c.status != "retired"])


def match_products(text: str) -> list[Product]:
    return _match(text, [p for p in load_products() if p.status == "active"])


def resolve_character(text: str, *, usable_only: bool = False) -> Character | None:
    """The single character this text refers to, or None if absent/ambiguous."""
    found = match_characters(text, usable_only=usable_only)
    return found[0] if len(found) == 1 else None


def resolve_product(text: str) -> Product | None:
    """The single product this text refers to, or None if absent/ambiguous."""
    found = match_products(text)
    return found[0] if len(found) == 1 else None


def get_character(slug_or_name: str) -> Character | None:
    key = slug_or_name.strip().lower()
    for c in load_characters():
        if key in {c.slug.lower(), c.name.lower(), c.full_name.lower()}:
            return c
    return None


def get_product(slug_or_name: str) -> Product | None:
    key = slug_or_name.strip().lower()
    for p in load_products():
        if key in {p.slug.lower(), p.name.lower()}:
            return p
    return None


# --------------------------------------------------------------------------- #
# rules the client actually cares about
# --------------------------------------------------------------------------- #


def banned_terms_in(text: str, product: Product | None = None) -> list[str]:
    """Forbidden claims present in a script or caption.

    The compliance floor: 'Halal', '90+ countries' and 'inspected by hand' were
    struck by the client, so they are checked in code rather than hoped away by a
    prompt. Product-specific bans stack on top of the global list.
    """
    haystack = text.lower()
    terms = [*global_claims_forbidden(), *(product.claims_forbidden if product else ())]
    seen: list[str] = []
    for term in terms:
        if term.lower() not in {s.lower() for s in seen} and _contains(haystack, term):
            seen.append(term)
    return seen


def suggested_presenters(product: Product, *, usable_only: bool = True) -> list[Character]:
    """Characters whose market fits the product — 'Asian presenter for duck' as code.

    Falls back to the product's own markets when no explicit presenter preference
    is set, and to the whole roster when nothing matches, so a caller always has
    someone to offer.
    """
    pool = [c for c in load_characters() if c.status != "retired"]
    if usable_only:
        pool = [c for c in pool if c.usable]
    wanted = set(product.presenter_preference or product.markets)
    matched = [c for c in pool if wanted & set(c.market_tags)]
    return matched or pool
