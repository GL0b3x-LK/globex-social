"""The image bank: naming a photograph we already own, instead of drawing a new one.

Every case here is one of the shapes the testers asked for out loud — "change
the picture to the hero lamb", "a picture of Priya holding the lamb" — plus the
two ways that lookup could do damage: resolving something when nothing was named
(silently swapping the picture on an unrelated edit), and resolving the bank when
the operator explicitly asked for something new.
"""

from __future__ import annotations

import pytest

from app.workflows import asset_bank

# --------------------------------------------------------------------------- #
# resolving a shot by name
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("change the picture to the hero lamb", "prod-lamb-hero.jpg"),
        ("use the veal hero shot", "prod-veal-hero.jpg"),
        ("QC hands on beef please", "prod-beef-qc-hands.jpg"),
        ("show the duck carton in the cold store", "pack-duck-carton-cold-store.jpg"),
        ("swap in the port ship photo", "brand-port-ship.jpg"),
    ],
)
def test_a_named_shot_resolves_to_that_file(request_text: str, expected: str) -> None:
    asset = asset_bank.resolve(request_text)
    assert asset is not None and asset.file == expected


def test_the_filename_outranks_a_stray_tag() -> None:
    """pack-duck-carton-hero-packed.jpg is tagged "retail" despite being a carton,
    and on equal weighting it beat the actual retail bag."""
    asset = asset_bank.resolve("use the duck retail bag shot")
    assert asset is not None and "retail" in asset.file


def test_an_unqualified_request_gets_the_plainest_shot() -> None:
    """Four duck retail files match "duck retail" equally well; the one without
    extra framing in its name is the one that was asked for."""
    asset = asset_bank.resolve("the duck retail pack")
    assert asset is not None and asset.file == "pack-duck-retail.jpg"


@pytest.mark.parametrize(
    "request_text",
    [
        "make it brighter",
        "shorten the headline",
        "more of a hero shot, warmer light",  # qualifiers only — names nothing
        "can you make the caption punchier",
    ],
)
def test_an_edit_that_names_nothing_resolves_to_nothing(request_text: str) -> None:
    """The dangerous failure: resolving on a framing word alone would swap the
    picture out from under an edit that never asked about the picture."""
    assert asset_bank.resolve(request_text) is None
    assert not asset_bank.resolve_refs(request_text)


# --------------------------------------------------------------------------- #
# person + product, the composition case
# --------------------------------------------------------------------------- #


def test_a_person_and_a_product_both_resolve_with_identity_first() -> None:
    refs = asset_bank.resolve_refs("I want a picture of Priya holding the lamb")
    assert refs.character is not None and refs.character.name == "Priya"
    assert refs.asset is not None and refs.asset.file == "prod-lamb-hero.jpg"
    # The model weights the first reference most, and a drifting face is the one
    # failure nobody accepts.
    assert refs.urls[0] == refs.character.reference_image_urls[0]
    assert refs.urls[1] == refs.asset.url
    assert refs.names == ["Priya", "lamb hero"]


def test_the_compose_prompt_names_both_references_by_position() -> None:
    refs = asset_bank.resolve_refs("Priya holding the lamb")
    prompt = asset_bank.compose_prompt("Priya holding the lamb in a chilled store", refs)
    assert "FIRST reference image" in prompt and "SECOND" in prompt
    assert "Priya" in prompt and "lamb" in prompt
    assert "graphic meat" in prompt  # the client's negatives ride along


def test_every_bank_asset_carries_a_hosted_url() -> None:
    """A local path cannot be handed to an image model — the whole point of the
    upload is that the reference is fetchable. Asserted as an invariant over
    whatever the pool holds, not against a fixed count: the pool grows every time
    shots are added, and a test that has to be edited to stay green is a test
    people edit without reading."""
    pool = asset_bank.assets()
    assert len(pool) > 90
    assert all(a.url.startswith("https://") for a in pool)
    assert all("/pool/" in a.url for a in pool)


# --------------------------------------------------------------------------- #
# "make me a new one" still means a new one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "request_text",
    [
        "generate a fresh lamb shot from scratch",
        "create a new image of a lamb",
        "make a new picture of the duck pack",
    ],
)
def test_asking_for_something_new_overrides_the_bank(request_text: str) -> None:
    assert asset_bank.wants_new_image(request_text)
    # The subject still resolves — it is the caller that must not use it.
    assert asset_bank.resolve(request_text) is not None


def test_an_ordinary_swap_is_not_read_as_a_request_for_something_new() -> None:
    assert not asset_bank.wants_new_image("change the picture to the hero lamb")


def test_the_catalogue_lists_the_names_to_ask_for() -> None:
    """An asset library nobody can enumerate is one nobody uses."""
    text = asset_bank.catalogue("lamb")
    assert "lamb hero" in text and "lamb qc hands" in text
    assert "Priya" in text  # the people are listed too
    assert "placeholder" not in text


def test_a_catalogue_entry_with_no_photograph_is_skipped_not_crashed_on() -> None:
    """The picker hands its result to read_bytes, so an entry with no file behind
    it stops a calendar post drafting at all. One did exist: a generated shot was
    rejected on inspection and deleted, and its entry stayed behind."""
    from app.workflows import scheduled

    scheduled._pool.cache_clear()
    try:
        pool = scheduled._pool()
        assert pool, "the pool must not be empty"
        assert all((scheduled._POOL_DIR / a["file"]).exists() for a in pool)
    finally:
        scheduled._pool.cache_clear()


# --------------------------------------------------------------------------- #
# the bank is asked before the image model, every time
# --------------------------------------------------------------------------- #


@pytest.fixture()
def image_request(monkeypatch):
    """_preview_generated with everything around it stubbed, recording what ran."""
    from types import SimpleNamespace

    from app.ai.generator import GeneratedPost
    from app.workflows import on_demand

    seen: dict = {"generated": False, "composed": False, "finalized": None, "asked_bank": False}

    async def fake_choose(request):
        seen["asked_bank"] = True
        return seen.get("bank_answer")

    async def fake_download(url):
        seen["downloaded"] = url
        return b"stored-jpeg"

    async def fake_generate(prompt, **kw):
        seen["generated"] = True
        return SimpleNamespace(ok=True, image_bytes=b"made-up-png")

    async def fake_edit_multi(urls, prompt, **kw):
        seen["composed"] = True
        return SimpleNamespace(ok=True, image_bytes=b"composed-png")

    async def fake_freeform(request, **kw):
        return GeneratedPost(
            caption="c",
            hashtags=["#Globex"],
            template_variant="ts_p1_bolddip",
            headline="H",
            rationale="r",
        )

    async def fake_finalize(phone, request, post, **kw):
        seen["finalized"] = kw

    async def noop(*a, **kw):
        return "SM1"

    monkeypatch.setattr(on_demand.asset_bank, "choose", fake_choose)
    monkeypatch.setattr(on_demand.image_gen, "download", fake_download)
    monkeypatch.setattr(on_demand.image_gen, "generate", fake_generate)
    monkeypatch.setattr(on_demand.image_gen, "edit_multi", fake_edit_multi)
    monkeypatch.setattr(on_demand.generator, "generate_freeform", fake_freeform)
    monkeypatch.setattr(on_demand, "_finalize_preview", fake_finalize)
    monkeypatch.setattr(on_demand.twilio_client, "try_send_text", noop)
    return seen


async def test_a_stored_photograph_is_used_instead_of_generating_one(image_request) -> None:
    """The rule: ask the bank first, every time. A photograph we own is free,
    instant, and shows our actual product rather than an imitation of it."""
    from app.workflows import on_demand

    image_request["bank_answer"] = asset_bank.get("brand-port-ship.jpg")
    await on_demand._preview_generated("whatsapp:+1", "a container ship at sunrise", "a ship")

    assert image_request["asked_bank"]
    assert image_request["generated"] is False, "the image model must not run"
    assert image_request["downloaded"].endswith("/pool/brand-port-ship.jpg")
    assert image_request["finalized"]["image_bytes"] == b"stored-jpeg"


async def test_nothing_suitable_in_the_bank_still_generates(image_request) -> None:
    """A near-miss is worse than a fresh image — the bank declining must fall
    straight through, not force a wrong photograph."""
    from app.workflows import on_demand

    image_request["bank_answer"] = None
    await on_demand._preview_generated(
        "whatsapp:+1", "a family sitting down to dinner", "a family at dinner"
    )

    assert image_request["asked_bank"]
    assert image_request["generated"] is True


async def test_a_declined_lookup_still_anchors_on_a_named_product(image_request) -> None:
    """ "Lamb on a marble table with rosemary" has no stored photograph, but we do
    own the lamb. Building from it beats building from the model's idea of it."""
    from app.workflows import on_demand

    image_request["bank_answer"] = None
    await on_demand._preview_generated(
        "whatsapp:+1", "lamb on a marble table with rosemary", "lamb on marble"
    )

    assert image_request["asked_bank"]
    assert image_request["composed"] is True
    assert image_request["generated"] is False


async def test_asking_outright_for_a_new_image_skips_the_lookup(image_request) -> None:
    from app.workflows import on_demand

    image_request["bank_answer"] = asset_bank.get("brand-port-ship.jpg")
    await on_demand._preview_generated(
        "whatsapp:+1", "generate a brand new image of a port", "a port"
    )

    assert image_request["asked_bank"] is False
    assert image_request["generated"] is True


async def test_naming_a_person_composes_rather_than_looking_one_up(image_request) -> None:
    """ "Priya holding the lamb" needs both references composed — there is no
    single stored photograph of it, and the lookup must not pre-empt that."""
    from app.workflows import on_demand

    image_request["bank_answer"] = asset_bank.get("prod-lamb-hero.jpg")
    await on_demand._preview_generated(
        "whatsapp:+1", "a picture of Priya holding the lamb", "Priya with lamb"
    )

    assert image_request["asked_bank"] is False
    assert image_request["composed"] is True
    assert image_request["generated"] is False
