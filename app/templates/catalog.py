"""Template registry: maps each AI-emitted ``template_variant`` to its HTML file,
theme, and declared Jinja slots. The renderer and tests both read from here, and
Phase 4 uses ``required_slots`` to know what to fill before rendering.
"""

from __future__ import annotations

from dataclasses import dataclass

# Logical canvas sizes (the renderer multiplies by device_scale_factor for crispness).
PLATFORM_DIMENSIONS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "landscape": (1200, 630),
    "story": (1080, 1920),
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
        description="Shipment volumes, market data, company impact numbers.",
    ),
    "founding_anniversary": TemplateSpec(
        "founding_anniversary",
        "founding_anniversary.html",
        "navy",
        required_slots=("figure",),
        optional_slots=("eyebrow", "figure_unit", "headline", "subhead", "sig"),
        description="Company founding-year milestone.",
    ),
    "holiday": TemplateSpec(
        "holiday",
        "holiday.html",
        "light",
        required_slots=("headline",),
        optional_slots=("eyebrow", "date_label", "subhead", "sig"),
        description="Date-specific holiday or observance.",
    ),
    "holiday_month_long": TemplateSpec(
        "holiday_month_long",
        "holiday_month_long.html",
        "light",
        required_slots=("headline",),
        optional_slots=("eyebrow", "month_label", "subhead", "sig"),
        description="Month-long observance (e.g. National Seafood Month).",
    ),
    "trade_show_pre": TemplateSpec(
        "trade_show_pre",
        "trade_show_pre.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "meta", "booth", "subhead", "sig"),
        description="Before a trade show — build anticipation.",
    ),
    "trade_show_during": TemplateSpec(
        "trade_show_during",
        "trade_show_during.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "photo", "sig"),
        needs_photo=True,
        description="Live at a trade show, usually over a photo.",
    ),
    "trade_show_post": TemplateSpec(
        "trade_show_post",
        "trade_show_post.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "sig"),
        description="After a trade show — thank attendees/partners.",
    ),
    "milestone": TemplateSpec(
        "milestone",
        "milestone.html",
        "navy",
        required_slots=("figure", "name"),
        optional_slots=("eyebrow", "figure_unit", "role", "subhead", "sig"),
        description="Employee anniversary (20+ years only).",
    ),
    "announcement": TemplateSpec(
        "announcement",
        "announcement.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "sig"),
        description="New hires, partnerships, general company news.",
    ),
    "product_spotlight": TemplateSpec(
        "product_spotlight",
        "product_spotlight.html",
        "light",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "product_image", "sig"),
        description="Protein/category feature; animal illustration or product photo.",
    ),
    "promotional": TemplateSpec(
        "promotional",
        "promotional.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "cta", "sig"),
        description="General promotion / call to action.",
    ),
    "branded_packaging": TemplateSpec(
        "branded_packaging",
        "branded_packaging.html",
        "navy",
        required_slots=("headline",),
        optional_slots=("eyebrow", "subhead", "package_image", "sig"),
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
}
