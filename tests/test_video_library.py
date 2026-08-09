"""Video engine libraries: roster integrity, product resolution, content rules."""

from __future__ import annotations

from app.video import library

EXAMPLE_BRIEF = (
    "create a video of john holding our raw chicken and talking about our process of shipping of it"
)


# --------------------------------------------------------------------------- #
# character roster
# --------------------------------------------------------------------------- #


def test_roster_is_ten_characters_five_each_gender() -> None:
    chars = library.load_characters()
    assert len(chars) == 10
    assert sum(c.gender == "male" for c in chars) == 5
    assert sum(c.gender == "female" for c in chars) == 5


def test_each_gender_covers_asian_african_and_caucasian() -> None:
    """The diversity spec Abdul set, asserted rather than assumed."""
    chars = library.load_characters()
    for gender in ("male", "female"):
        ethnicities = {c.ethnicity for c in chars if c.gender == gender}
        assert {"east_asian", "african", "caucasian"} <= ethnicities, gender
        assert len(ethnicities) == 5, f"{gender} should span five distinct backgrounds"


def test_ages_are_spread_not_clustered() -> None:
    ages = sorted(c.age for c in library.load_characters())
    assert ages[0] < 30 and ages[-1] > 50
    assert len(set(ages)) == 10  # every character a different age


def test_slugs_and_names_are_unique() -> None:
    chars = library.load_characters()
    assert len({c.slug for c in chars}) == 10
    assert len({c.name.lower() for c in chars}) == 10


def test_every_character_has_what_generation_needs() -> None:
    for c in library.load_characters():
        assert c.visual_prompt and c.voice_direction and c.persona, c.slug
        assert c.market_tags and c.setting_affinity, c.slug


def test_roster_is_approved_with_an_audit_trail() -> None:
    """Len approved all 10 on 2026-08-09. Approval is recorded, not assumed."""
    for c in library.load_characters():
        assert not c.is_real_person, c.slug
        assert c.status == "approved", c.slug
        assert c.approved_at, f"{c.slug} is approved but has no approval date"
        assert c.usable, c.slug
    assert library.resolve_character(EXAMPLE_BRIEF, usable_only=True) is not None


def test_a_draft_character_still_cannot_front_a_video() -> None:
    """The gate survives approval: a NEW persona re-enters as draft and must be
    approved on its own. Approving the seed roster must not approve future ones."""
    john = library.get_character("john")
    assert john is not None
    newcomer = library.Character(**{**john.__dict__, "slug": "john", "status": "draft"})
    assert newcomer.has_references  # face and voice ready...
    assert not newcomer.usable  # ...but unapproved, so unusable


def test_a_real_person_needs_consent_to_be_usable() -> None:
    john = library.get_character("john")
    assert john is not None
    assert not library.Character(
        **{**john.__dict__, "status": "approved", "is_real_person": True}
    ).usable
    consented = library.Character(
        **{
            **john.__dict__,
            "status": "approved",
            "is_real_person": True,
            "likeness_consent": {"signed": "2026-08-09"},
        }
    )
    assert consented.usable


# --------------------------------------------------------------------------- #
# resolution — the brief in the user's own words
# --------------------------------------------------------------------------- #


def test_resolves_john_from_the_example_brief() -> None:
    assert library.resolve_character(EXAMPLE_BRIEF) is not None
    assert library.resolve_character(EXAMPLE_BRIEF).slug == "john"  # type: ignore[union-attr]


def test_full_name_and_alias_both_resolve() -> None:
    assert library.resolve_character("a video with Mei Lin Tan").slug == "mei"  # type: ignore[union-attr]
    assert library.resolve_character("get Boateng to explain the cold chain").slug == "kwame"  # type: ignore[union-attr]


def test_unknown_person_resolves_to_nobody() -> None:
    assert library.resolve_character("make a video with Gerald") is None


def test_raw_chicken_is_ambiguous_so_we_ask_instead_of_guessing() -> None:
    """Six chicken lines — picking one silently would be the wrong SKU eventually."""
    candidates = library.match_products(EXAMPLE_BRIEF)
    assert len(candidates) > 1
    assert library.resolve_product(EXAMPLE_BRIEF) is None
    assert all("chicken" in p.name.lower() for p in candidates)


def test_specific_product_phrases_resolve_cleanly() -> None:
    assert library.resolve_product("a video about our chicken breasts").slug == "chicken-breast"  # type: ignore[union-attr]
    assert library.resolve_product("show the drumsticks").slug == "chicken-drumstick"  # type: ignore[union-attr]
    assert library.resolve_product("something on duck for Lunar New Year").slug == "duck-retail"  # type: ignore[union-attr]


def test_matching_respects_word_boundaries() -> None:
    """'duck' inside 'ducking' is not a product reference."""
    assert library.match_products("audio ducking under the voiceover") == []


def test_no_product_reference_returns_nothing() -> None:
    assert library.match_products("a video about the trade show in Bogota") == []


# --------------------------------------------------------------------------- #
# content rules as data
# --------------------------------------------------------------------------- #


def test_global_no_say_list_is_enforced() -> None:
    assert library.banned_terms_in("Our plants are Halal certified") == ["Halal"]
    assert library.banned_terms_in("We ship to 90+ countries every week") == ["90+ countries"]
    assert library.banned_terms_in("Every bird is inspected by hand") == ["inspected by hand"]


def test_approved_phrasing_passes_the_linter() -> None:
    clean = "Quality Control signs off before anything ships, and we ship globally"
    assert library.banned_terms_in(clean) == []


def test_product_bans_stack_on_the_global_list() -> None:
    duck = library.get_product("duck-retail")
    assert duck is not None
    found = library.banned_terms_in("Halal duck shipped everywhere", duck)
    assert "Halal" in found


def test_never_raw_hero_products_only_front_packaging() -> None:
    """Len rejected graphic carcass imagery — no raw shot may lead these posts."""
    flagged = [p for p in library.load_products() if p.visual_rules.get("never_raw_hero")]
    assert {p.slug for p in flagged} == {"whole-chicken", "chicken-gizzard", "chicken-frame"}
    for p in flagged:
        assert p.product_shot_files, f"{p.slug} has a raw shot on file..."
        assert not set(p.hero_files) & set(p.product_shot_files), (
            f"...but {p.slug} must never front a video with it"
        )
        assert p.hero_files, f"{p.slug} still needs a packaged image to lead with"


def test_duck_prefers_an_asian_presenter() -> None:
    duck = library.get_product("duck-carton")
    assert duck is not None
    suggested = library.suggested_presenters(duck, usable_only=False)
    assert suggested
    assert all("asia" in c.market_tags for c in suggested)


def test_every_referenced_asset_file_exists() -> None:
    for p in library.load_products():
        for path in p.asset_paths((*p.pack_shot_files, *p.product_shot_files)):
            assert path.exists(), f"{p.slug} -> {path.name}"


def test_products_cover_what_we_actually_have_photos_for() -> None:
    products = library.load_products()
    assert len(products) >= 10
    assert {p.category for p in products} >= {"poultry", "duck", "packaging"}
    assert all(p.hero_files for p in products), "every product needs at least one usable image"


def test_wardrobe_and_generation_rules_are_available_to_prompts() -> None:
    assert "logo" in library.wardrobe_rule().lower()
    rules = " ".join(library.generation_rules()).lower()
    assert "steam" in rules and "carcass" in rules


# --------------------------------------------------------------------------- #
# generate once, reuse forever
# --------------------------------------------------------------------------- #


def test_every_character_has_stored_reference_shots() -> None:
    """The roster's identity lives in files on disk, not in a prompt re-run."""
    for c in library.load_characters():
        assert c.has_references, f"{c.slug} has no generated reference shots"
        assert c.primary_reference is not None, f"{c.slug} is missing its front portrait"
        assert c.primary_reference.name == "front.jpg"


def test_a_character_without_references_can_never_be_used() -> None:
    """Approval alone is not enough — without a stored face, a video would have to
    re-generate one from the prompt, and it would not look like the same person."""
    john = library.get_character("john")
    assert john is not None
    approved = library.Character(**{**john.__dict__, "status": "approved"})
    assert approved.usable  # has references on disk

    ghost = library.Character(**{**john.__dict__, "slug": "nobody", "status": "approved"})
    assert not ghost.has_references
    assert not ghost.usable


def test_reference_paths_are_stable_across_calls() -> None:
    c = library.get_character("mei")
    assert c is not None
    assert c.reference_paths == c.reference_paths
    assert all(p.suffix == ".jpg" and p.exists() for p in c.reference_paths)


def test_reference_urls_are_recorded_front_first() -> None:
    """Hosted URLs are what the generator fetches; identity anchor must be first."""
    for c in library.load_characters():
        assert c.reference_image_urls, f"{c.slug} has no hosted references"
        assert c.reference_image_urls[0].endswith("front.jpg"), c.slug
        assert len(c.reference_image_urls) == len(c.reference_paths), c.slug


def test_every_character_has_a_locked_voice() -> None:
    """A voice, like a face, is designed once and referenced by id forever."""
    for c in library.load_characters():
        assert c.voice_id, f"{c.slug} has no locked voice_id"
        assert len(c.voice_id) > 10, c.slug
    assert len({c.voice_id for c in library.load_characters()}) == 10  # no shared voices


def test_all_voices_are_american_and_never_accent_matched_to_appearance() -> None:
    """Globex is a US company; the client rejected accent variation outright."""
    forbidden = ("inflected", "east asian accent", "latin accent", "indian", "west african")
    for c in library.load_characters():
        direction = c.voice_direction.lower()
        assert "american" in direction, f"{c.slug} voice is not American"
        for term in forbidden:
            assert term not in direction, f"{c.slug} voice still carries '{term}'"


def test_voice_description_always_pins_the_accent() -> None:
    from app.video import voices

    c = library.get_character("wei")
    assert c is not None
    assert "American English" in voices.voice_description(c.voice_direction, c.role)


def test_preview_line_obeys_the_no_say_list() -> None:
    from app.video import voices

    assert library.banned_terms_in(voices.PREVIEW_TEXT) == []
    assert 100 <= len(voices.PREVIEW_TEXT) <= 1000  # ElevenLabs design constraint
