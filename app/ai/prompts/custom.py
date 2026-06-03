"""Catch-all: Karen sends a photo + freeform text that doesn't fit other categories."""

CUSTOM_PROMPT = """TASK: Write a post from Karen's freeform request.

The context is whatever Karen sent — a photo and/or a description that doesn't fit a specific category. Interpret her intent and produce an on-brand Globex post.

template_variant: "custom"

- Work entirely from what she provided. If a photo is attached, describe/lean on what is actually visible — never hallucinate specifics (people, places, numbers) that aren't there or given.
- Apply all brand rules. When her request is sparse, keep the post simple and safe rather than padding it with invented detail.
- If the request is genuinely ambiguous, choose the most reasonable on-brand interpretation and reflect that choice in the rationale.
- 3-6 hashtags including #GlobexInternational."""
