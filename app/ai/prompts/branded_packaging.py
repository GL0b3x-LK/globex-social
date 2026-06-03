"""The finite pool of 20 pre-designed brand/packaging rotation posts."""

BRANDED_PACKAGING_PROMPT = """TASK: Produce the caption for a branded-packaging rotation post.

These are a fixed set of 20 pre-designed packaging/brand posts rotated on a schedule. The context provides a caption_template or angle for this specific slot, plus the packaging image reference.

template_variant: "branded_packaging"

- LOWER creativity, HIGHER consistency. If a caption_template is provided, stay close to it — light polish only, keep it on-brand and on-message. Do not reinvent it.
- Reinforce Globex's branded packaging quality and consistency. Keep it short.
- 3-6 hashtags including #GlobexInternational. Keep hashtags stable/consistent across the rotation where the template suggests them."""
