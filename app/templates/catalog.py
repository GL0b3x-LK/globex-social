"""Template registry: maps each AI-emitted ``template_variant`` to its HTML file,
theme, and declared Jinja slots. The renderer and tests both read from here, and
Phase 4 uses ``required_slots`` to know what to fill before rendering.
"""

from __future__ import annotations

from dataclasses import dataclass

# Logical canvas sizes (the renderer multiplies by device_scale_factor for crispness).
# "portrait" is the finals' canvas: 1080x1350 @2x -> 2160x2700, matching the
# client-approved reference PNGs.
PLATFORM_DIMENSIONS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "landscape": (1200, 630),
    "story": (1080, 1920),
    "portrait": (1080, 1350),
}


@dataclass(frozen=True)
class TemplateSpec:
    variant: str
    file: str
    theme: str  # "navy" | "light" | "cyan"
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    needs_photo: bool = False
    description: str = ""
    canvas: str = "square"  # PLATFORM_DIMENSIONS key; finals render 4:5 portrait
    # The small cyan label above the headline, when the operator/AI hasn't set
    # one. It used to live as a Jinja `default(...)` inside each HTML file, where
    # nothing in Python could read it — so the copy on the image was invisible to
    # the code, unchangeable by an edit, and wrong whenever the template was
    # reused (a company birthday rendered on MS-3 read "WORK ANNIVERSARY").
    # Empty = this template has no eyebrow, or shows none unless asked.
    eyebrow_default: str = ""

    def all_slots(self) -> tuple[str, ...]:
        base = ("sig",)  # every template accepts an optional footer signature
        return tuple(dict.fromkeys(self.required_slots + self.optional_slots + base))


TEMPLATES: dict[str, TemplateSpec] = {
    "stats": TemplateSpec(
        "stats",
        "stats.html",
        "navy",
        required_slots=("figure",),
        optional_slots=("eyebrow", "figure_unit", "subhead", "stat_items", "sig"),
        eyebrow_default="Globex by the numbers",
        description="Shipment volumes, market data, company impact numbers.",
    ),
    "founding_anniversary": TemplateSpec(
        "founding_anniversary",
        "founding_anniversary.html",
        "navy",
        required_slots=("figure",),
        optional_slots=("eyebrow", "figure_unit", "headline", "subhead", "sig"),
        eyebrow_default="Celebrating",
        description="Company founding-year milestone.",
    ),
    "holiday": TemplateSpec(
        "holiday",
        "holiday.html",
        "light",
        required_slots=("headline",),
        optional_slots=("eyebrow", "date_label", "subhead", "sig"),
        eyebrow_default="Today",
        description="Date-specific holiday or observance.",
    ),
    "holiday_month_long": TemplateSpec(
        "holiday_month_long",
        "holiday_month_long.html",
        "light",
        required_slots=("headline",),
        optional_slots=("eyebrow", "month_label", "subhead", "sig"),
        eyebrow_default="All month",
        description="Month-long observance (e.g. National Seafood Month).",
    ),
    "trade_show_pre": TemplateSpec(
        "trade_show_pre",
        "trade_show_pre.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "meta", "booth", "subhead", "sig"),
        eyebrow_default="Meet us at",
        description="Before a trade show — build anticipation.",
    ),
    "trade_show_during": TemplateSpec(
        "trade_show_during",
        "trade_show_during.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "photo", "sig"),
        needs_photo=True,
        eyebrow_default="Live at",
        description="Live at a trade show, usually over a photo.",
    ),
    "trade_show_post": TemplateSpec(
        "trade_show_post",
        "trade_show_post.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "sig"),
        eyebrow_default="Thank you",
        description="After a trade show — thank attendees/partners.",
    ),
    "milestone": TemplateSpec(
        "milestone",
        "milestone.html",
        "navy",
        required_slots=("figure", "name"),
        optional_slots=("eyebrow", "figure_unit", "role", "subhead", "sig"),
        eyebrow_default="Celebrating",
        description="Employee anniversary (20+ years only).",
    ),
    "announcement": TemplateSpec(
        "announcement",
        "announcement.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "sig"),
        eyebrow_default="Announcement",
        description="New hires, partnerships, general company news.",
    ),
    "product_spotlight": TemplateSpec(
        "product_spotlight",
        "product_spotlight.html",
        "light",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "product_image", "sig"),
        eyebrow_default="Product spotlight",
        description="Protein/category feature; animal illustration or product photo.",
    ),
    "promotional": TemplateSpec(
        "promotional",
        "promotional.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "cta", "sig"),
        eyebrow_default="Globex",
        description="General promotion / call to action.",
    ),
    "branded_packaging": TemplateSpec(
        "branded_packaging",
        "branded_packaging.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "package_image", "sig"),
        eyebrow_default="Globex packaging",
        description="Rotating packaging colorway showcase.",
    ),
    "custom": TemplateSpec(
        "custom",
        "custom.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "photo", "sig"),
        needs_photo=True,
        description="Karen's photo + description; AI writes the copy.",
    ),
    "polaroid": TemplateSpec(
        "polaroid",
        "polaroid.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("photo", "sig"),
        needs_photo=True,
        description="Casual Polaroid-style snapshot (a photo or generated image) on a navy frame.",
    ),
    "quote_card": TemplateSpec(
        "quote_card",
        "quote_card.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("sig",),
        description="A short quote or statement as a clean navy typographic card (no photo).",
    ),
    # ---- Final approved set (July 30 2026 sign-off) — 4:5 portrait, photo-first. ----
    # These four are the ONLY templates client-facing posts may use; the calendar's
    # Template column maps onto them 1:1.
    "ts_p1_bolddip": TemplateSpec(
        "ts_p1_bolddip",
        "ts_p1_bolddip.html",
        "navy",
        required_slots=("photo", "headline"),
        optional_slots=("subline_strong", "subline_soft", "pill"),
        needs_photo=True,
        canvas="portrait",
        description=(
            "FINAL. Photo-first with a bold rounded dip at the photo's bottom-left; "
            "ALL-CAPS letterspaced headline, optional cyan booth pill, logo bottom-right."
        ),
    ),
    "ts_p2_cut_navyborder": TemplateSpec(
        "ts_p2_cut_navyborder",
        "ts_p2_cut_navyborder.html",
        "navy",
        required_slots=("photo", "headline"),
        optional_slots=("subline_strong", "subline_soft"),
        needs_photo=True,
        canvas="portrait",
        description=(
            "FINAL. Square-cut photo in a thin navy frame with a full-width cyan "
            "divider; bold title-case headline, centered logo."
        ),
    ),
    "ts_p3_editorial": TemplateSpec(
        "ts_p3_editorial",
        "ts_p3_editorial.html",
        "navy",
        required_slots=("photo", "headline"),
        optional_slots=("meta",),
        needs_photo=True,
        canvas="portrait",
        description=(
            "FINAL. Editorial masthead on top (headline + date/location/booth meta "
            "with cyan bullets), photo below, centered logo in the bottom band."
        ),
    ),
    "ms_3_anniv_photo": TemplateSpec(
        "ms_3_anniv_photo",
        "ms_3_anniv_photo.html",
        "navy",
        required_slots=("photo", "name"),
        optional_slots=("years", "eyebrow", "message", "role"),
        needs_photo=True,
        canvas="portrait",
        # Right for the card's own purpose, wrong the moment the layout is reused
        # for a company milestone — which is why the label has to be editable
        # rather than baked in.
        eyebrow_default="Work Anniversary",
        description=(
            "FINAL. Employee-milestone card: full-bleed portrait, navy panel, cyan "
            "hairline frame and years badge (20+ year anniversaries only)."
        ),
    ),
}

# The calendar's Template column -> catalog variant. These are also the names the
# client uses in conversation, so they are what the free-form prompt emits and
# what an operator can ask for by name over WhatsApp.
CALENDAR_TEMPLATE_ALIASES: dict[str, str] = {
    "TS-p1-bolddip_4x5": "ts_p1_bolddip",
    "TS-p2-cut-navyborder_4x5": "ts_p2_cut_navyborder",
    "TS-p3-editorial_4x5": "ts_p3_editorial",
    "MS-3-anniv-photo_4x5": "ms_3_anniv_photo",
}

# The July 30 2026 sign-off: the only templates a client-facing post may use.
# Everything else in TEMPLATES is demo-era and renders from _base.css in
# Montserrat, which is how a from-scratch post came back in the wrong typeface.
FINAL_VARIANTS: tuple[str, ...] = tuple(CALENDAR_TEMPLATE_ALIASES.values())

# The workhorse of the four: photo-first and happy with any subject, so it is
# what an unknown or unusable variant resolves to.
DEFAULT_FINAL = "ts_p1_bolddip"


def is_final(variant: str) -> bool:
    return variant in FINAL_VARIANTS


# How the templates are actually referred to in a WhatsApp message — the client
# names ("TS-p2-cut-navyborder_4x5"), the short forms ("p2", "TS-2"), and the
# descriptive handles ("the navy border one", "the editorial layout").
_TEMPLATE_HANDLES: tuple[tuple[str, str], ...] = (
    (r"\b(?:ts[-_ ]?)?p1\b|\bbold[-_ ]?dip\b", "ts_p1_bolddip"),
    (r"\b(?:ts[-_ ]?)?p2\b|\bnavy[-_ ]?border\b", "ts_p2_cut_navyborder"),
    (r"\b(?:ts[-_ ]?)?p3\b|\beditorial\b", "ts_p3_editorial"),
    (r"\bms[-_ ]?3\b|\banniv[-_ ]?photo\b", "ms_3_anniv_photo"),
)


def named_template(text: str) -> str | None:
    """The approved template this message names, if it names exactly one.

    A named template is an instruction, not a suggestion — but the model treated
    it as one: asked in so many words for TS-p2-cut-navyborder_4x5, it emitted
    TS-p1 (its prompt calls TS-p1 "the workhorse", and the workhorse won). The
    operator then reported wrong fonts, a wrong logo position and a missing
    divider — every one of them just TS-p1 being TS-p1. So the name is applied
    in code after generation, where the model gets no vote.

    Naming TWO templates returns None: "more like p2 than p1" is a comparison,
    and guessing which one they meant is the model's mistake all over again.
    """
    import re as _re

    lowered = (text or "").lower()
    hits = {v for pattern, v in _TEMPLATE_HANDLES if _re.search(pattern, lowered)}
    return hits.pop() if len(hits) == 1 else None
