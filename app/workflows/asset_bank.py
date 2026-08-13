"""The image bank: the photographs we already own, reachable by name.

A hundred curated shots sit in ``app/data/asset_pool`` and ten approved people
sit in the character library, every one of them hosted and addressable by URL.
Until now the pool was consulted exactly once — when a calendar post was first
drafted — and the characters only by the video engine. Neither could be asked
for by name, so "use the hero lamb shot" had nowhere to go and the only way to
change a picture was to have an image model redraw it.

This module is the missing lookup. It answers two questions:

  * *which* photograph does this sentence mean — "hero lamb", "the duck retail
    bag", "QC hands on beef" — resolved against the filenames, the curated tags
    and the product aliases the library already knows;
  * *who* does it mean — "Priya", "John" — resolved to that person's stored
    front portrait, the identity anchor the video engine already relies on.

Both answers come back as hosted URLs, which is what makes the interesting case
work: "Priya holding the lamb" resolves to two real photographs and goes to the
image model as a multi-reference composition, so it places things we own rather
than inventing a person and a cut of meat from a description.

Pure data and matching — nothing here spends money or calls a provider.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel, Field

from app.ai.client import generate_structured
from app.db import storage
from app.logging_config import get_logger
from app.video import library

log = get_logger("app.workflows.asset_bank")

POOL_JSON = library.ASSET_POOL / "pool.json"

# Filename prefixes that say what KIND of file it is, not what is in it.
_PREFIXES = frozenset({"prod", "pack", "brand", "placeholder", "jpg", "png"})

# Words describing the framing of a shot rather than its subject. They choose
# BETWEEN shots of the same thing ("hero lamb" vs "lamb in the cold store") but
# can never make a match alone — otherwise "make it brighter, more of a hero
# shot" would resolve to whatever happens to be tagged "hero" and silently swap
# the picture for an unrelated one.
_QUALIFIERS = frozenset(
    {
        "hero",
        "qc",
        "quality",
        "control",
        "hands",
        "inspection",
        "cold",
        "store",
        "storage",
        "packed",
        "packaging",
        "tray",
        "closeup",
        "export",
        "warehouse",
        "pallet",
        "logistics",
        "shot",
        "shots",
        "image",
        "images",
        "picture",
        "photo",
        "photograph",
    }
)

# Asking for something new, in the operator's own words. These override the bank
# even when a subject resolves: "generate a fresh lamb shot" means generate.
_FROM_SCRATCH = (
    "from scratch",
    "generate",
    "generated",
    "create a new",
    "create new",
    "make a new",
    "brand new",
    "new image",
    "new picture",
    "invent",
    "imagine",
)

_SPLIT = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> frozenset[str]:
    return frozenset(w for w in _SPLIT.split((text or "").lower()) if w)


@dataclass(frozen=True)
class Asset:
    """One photograph in the pool, with its hosted URL."""

    file: str
    tags: tuple[str, ...]
    url: str

    @property
    def label(self) -> str:
        """What a person would call it: "prod-lamb-hero.jpg" -> "lamb hero"."""
        stem = self.file.rsplit(".", 1)[0]
        return " ".join(w for w in _SPLIT.split(stem) if w and w not in _PREFIXES)

    @property
    def _terms(self) -> frozenset[str]:
        return _words(self.file) | _words(" ".join(self.tags))

    @property
    def subjects(self) -> frozenset[str]:
        """The words that actually identify what is pictured."""
        return self._terms - _QUALIFIERS - _PREFIXES

    @property
    def named_subjects(self) -> frozenset[str]:
        """Subject words in the FILENAME, which is curated; the tags are not.

        ``pack-duck-carton-hero-packed.jpg`` carries the tag "retail" despite
        being a carton, and on equal weighting it beat the actual retail bag for
        "the duck retail bag". What a file is called is stronger evidence than
        what it was tagged.
        """
        return _words(self.file) - _QUALIFIERS - _PREFIXES

    @property
    def qualifiers(self) -> frozenset[str]:
        return self._terms & _QUALIFIERS

    @property
    def specificity(self) -> int:
        """How many words the filename spends. Fewer = the plainer shot, which is
        what an unqualified request ("the duck retail bag") should land on."""
        return len(_words(self.file) - _PREFIXES)


@lru_cache(maxsize=1)
def assets() -> tuple[Asset, ...]:
    doc = json.loads(POOL_JSON.read_text(encoding="utf-8"))
    return tuple(
        Asset(
            file=entry["file"],
            tags=tuple(entry.get("tags") or ()),
            # The URL recorded at upload time wins; otherwise it is derived, so a
            # shot dropped into the pool without re-running the upload script
            # still resolves by name instead of vanishing from the bank.
            url=entry.get("url") or storage.public_url(f"pool/{entry['file']}"),
        )
        for entry in doc["assets"]
    )


def get(file: str) -> Asset | None:
    return next((a for a in assets() if a.file == file), None)


def find(text: str, *, limit: int = 5) -> list[Asset]:
    """Pool shots this sentence could mean, best first.

    Scoring is deliberately lopsided: a subject word in the filename beats one in
    the tags, which beats a framing word. So "hero lamb" lands on the lamb hero
    shot rather than on whichever hero shot carries the most tags, and ties break
    towards the plainest file rather than alphabetically.
    """
    words = _words(text)
    scored: list[tuple[float, int, str, Asset]] = []
    for asset in assets():
        if asset.file.startswith("placeholder"):
            continue
        subject_hits = len(asset.subjects & words)
        if not subject_hits:
            continue
        score = (
            len(asset.named_subjects & words) * 6.0
            + subject_hits * 2.0
            + len(asset.qualifiers & words)
        )
        scored.append((score, asset.specificity, asset.file, asset))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    return [asset for _score, _spec, _file, asset in scored[:limit]]


def resolve(text: str) -> Asset | None:
    """The single pool shot this sentence means, or None."""
    hits = find(text, limit=1)
    return hits[0] if hits else None


def wants_new_image(text: str) -> bool:
    """True when the operator asked for something made, not something we own."""
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in _FROM_SCRATCH)


@dataclass(frozen=True)
class Refs:
    """The photographs a request names, ready to hand to the image model."""

    character: library.Character | None = None
    asset: Asset | None = None

    @property
    def urls(self) -> list[str]:
        """Identity first — the same order the video keyframes use, because the
        model weights the first reference most and a face that drifts is the one
        failure nobody accepts."""
        out: list[str] = []
        if self.character is not None:
            out.append(self.character.reference_image_urls[0])
        if self.asset is not None:
            out.append(self.asset.url)
        return out

    @property
    def names(self) -> list[str]:
        out: list[str] = []
        if self.character is not None:
            out.append(self.character.name)
        if self.asset is not None:
            out.append(self.asset.label)
        return out

    def __bool__(self) -> bool:
        return bool(self.urls)


def resolve_refs(text: str) -> Refs:
    """Everything in the bank this sentence names — a person, a shot, or both.

    A product named through the library ("duck retail bag") resolves via its own
    aliases first, then falls back to matching the pool directly, so both the
    catalogue's vocabulary and the shot filenames are reachable by name.
    """
    character = library.resolve_character(text, usable_only=True)
    if character is not None and not character.reference_image_urls:
        character = None  # approved but never hosted; nothing to reference

    asset = resolve(text)
    if asset is None:
        product = library.resolve_product(text)
        if product is not None and product.hero_files:
            asset = get(product.hero_files[0])
    return Refs(character=character, asset=asset)


# The look is fixed here rather than left to the request: these are photographs
# of a real operation, and the client rejected studio gloss and graphic meat in
# the design rounds. Lifted from the video keyframes, which pass the same kind
# of payload to the same model.
_STYLE = (
    "Photorealistic documentary still from a working food business, natural available "
    "light, honest shallow depth of field. Not a studio shot, not stock photography."
)
_NEGATIVES = (
    "Do not include: any logo, brand mark, wordmark or printed signage that is not "
    "already on the referenced packaging; steam, smoke, haze or floating particles; "
    "dramatic spotlights or lens flare; raw carcasses, blood or graphic meat; "
    "cropped or obscured faces."
)


def compose_prompt(request: str, refs: Refs) -> str:
    """The instruction that goes with the reference images.

    The references are named as references, not described: the whole reason both
    photographs are attached is so the model reproduces the person and the
    product it can see rather than a fresh invention of each.
    """
    parts: list[str] = []
    if refs.character is not None and refs.asset is not None:
        parts.append(
            f"Use the FIRST reference image for the person ({refs.character.name}) and the "
            f"SECOND for the product ({refs.asset.label}). Keep the person's face, build and "
            "clothing exactly as shown, and keep the product and any packaging exactly as "
            "shown — same shape, same colours, same printing."
        )
    elif refs.character is not None:
        parts.append(
            f"Use the reference image for the person ({refs.character.name}). Keep their face, "
            "build and clothing exactly as shown."
        )
    elif refs.asset is not None:
        parts.append(
            f"Use the reference image for the product ({refs.asset.label}). Keep it and any "
            "packaging exactly as shown — same shape, same colours, same printing."
        )
    parts.append(f"The picture to make: {request.strip()}")
    parts.append(_STYLE)
    parts.append(_NEGATIVES)
    return "\n\n".join(parts)


class _BankChoice(BaseModel):
    file: str | None = Field(
        default=None,
        description=(
            "The exact filename of the stored photograph that satisfies the request, "
            "or null if none of them does."
        ),
    )
    reason: str = Field(description="One short sentence: why it fits, or what is missing.")


_CHOOSE_PROMPT = """You are choosing whether a photograph Globex ALREADY OWNS can serve a request, or whether a new image has to be generated.

Below is every photograph in the library. Each line is `filename — what it shows`.

{catalogue}

Answer with the filename ONLY if that photograph genuinely depicts what was asked for. A real photograph we own beats a generated one every time: it is free, instant, and it shows our actual product and packaging rather than an imitation of it.

But a near-miss is worse than a fresh image. Choose null when the request names a subject, setting, action or prop the library does not have. "Chicken breasts on a barbecue with lemon" is NOT satisfied by a chicken breast pack shot in a cold store — same product, wrong picture. "A container ship at sunrise" IS satisfied by the port ship photograph.

Judge the whole scene, not just the product word."""


async def choose(request: str) -> Asset | None:
    """The stored photograph that answers this request, or None to generate one.

    Tag overlap is too blunt to make this call — "chicken breasts on a barbecue"
    matches every chicken breast shot we own on the word "chicken" and none of
    them on the barbecue. So the library is small enough (about a hundred short
    labels) to simply show the model and ask, which costs one cheap call and
    saves an image generation every time it lands.

    Any failure returns None: falling through to generation is the behaviour we
    already had, and a lookup that breaks must not take image posts down with it.
    """
    lines = "\n".join(
        f"{a.file} — {a.label}" for a in assets() if not a.file.startswith("placeholder")
    )
    try:
        choice = await generate_structured(
            system=_CHOOSE_PROMPT.format(catalogue=lines),
            user_content=f"The request:\n{request}",
            output_model=_BankChoice,
            tool_name="choose_stored_photo",
            tool_description="Name the stored photograph that fits, or null if none does.",
            max_tokens=400,
        )
    except Exception as exc:  # noqa: BLE001 — generation is the fallback, not an error
        log.error("bank lookup failed; generating instead", extra={"error": str(exc)[:200]})
        return None
    if not choice.file:
        log.info("nothing in the bank fits; generating", extra={"why": choice.reason[:160]})
        return None
    asset = get(choice.file.strip())
    if asset is None:
        log.warning("bank lookup named an unknown file", extra={"file": choice.file[:80]})
        return None
    log.info("using a stored photograph", extra={"file": asset.file, "why": choice.reason[:160]})
    return asset


def catalogue(subject: str = "") -> str:
    """The bank, written out — what to ask for, in the names it answers to.

    An asset library nobody can enumerate is an asset library nobody uses: the
    operator has to know "hero lamb" is a thing they can say.
    """
    people = [c.name for c in library.load_characters() if c.usable]
    shots = find(subject, limit=40) if subject else list(assets())
    labels = sorted({a.label for a in shots if not a.file.startswith("placeholder")})
    lines = [f"People ({len(people)}): " + ", ".join(sorted(people))]
    if subject:
        lines.append(f'Shots matching "{subject.strip()}" ({len(labels)}):')
    else:
        lines.append(f"Shots ({len(labels)}):")
    lines.extend(f"• {label}" for label in labels)
    return "\n".join(lines)
