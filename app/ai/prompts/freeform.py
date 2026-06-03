"""Free-form path: Karen sends an open request (maybe a photo); the model picks the
best template and writes the post. Used by the on-demand WhatsApp flow, where the
content category isn't known up front (the scheduler path knows it and uses the
category-specific prompts instead)."""

FREEFORM_PROMPT = """TASK: Karen sent a free-form request (and maybe a photo). Decide the best post type, then write the post.

Choose ONE template_variant that best fits the request:
- "stats" — a number / impact stat (shipments, countries served, volumes).
- "trade_show_pre" / "trade_show_during" / "trade_show_post" — a trade show before it / live on the floor (usually a photo) / after it.
- "holiday" / "holiday_month_long" — an industry or cultural holiday (a single day) or a month-long observance.
- "announcement" — a partnership, new market, hire, or company milestone news (NOT external current-events news).
- "product_spotlight" — feature a protein / category (poultry, beef, pork, seafood, duck, grains, pet food).
- "promotional" — a general brand / capability message or call to action.
- "custom" — Karen attached a photo to build around, or nothing else fits cleanly.

Do NOT choose "milestone" or "founding_anniversary" here — those are generated automatically from employee / company data, never from a free-form request.

If a photo is attached, prefer "trade_show_during" or "custom" and write to complement the image.
Use ONLY the specifics Karen provided. If she names a show, partner, or number, use it exactly; if a detail (booth number, exact date) isn't given, write around it — never invent one."""
