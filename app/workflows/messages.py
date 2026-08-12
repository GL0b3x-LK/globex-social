"""Canned WhatsApp replies + the preview-caption builder. No I/O, no cycles."""

from __future__ import annotations

from typing import Any

from app.ai.generator import GeneratedPost
from app.publishing import platforms as plat

GREETING = (
    'Hi! Tell me what you\'d like to post — e.g. "post about us at Gulfood" or a '
    "shipment stat — and I'll draft it for your approval."
)
NUDGE_PENDING = (
    "You've got a draft waiting. Reply *approve* to publish, send an edit "
    '(e.g. "make it shorter"), or *cancel*.'
)
NOTHING_PENDING = (
    "Nothing's in progress right now. Tell me what you'd like to post and I'll draft it."
)
CLARIFY = (
    "I didn't quite catch that. Tell me what to post — a trade show, a stat, a holiday, "
    "an announcement — and I'll draft it."
)

# --- Voice notes ---
VOICE_NO_SPEECH = (
    "🎤 I couldn't make out any speech in that voice note. Mind resending it, or just "
    "type what you'd like?"
)
VOICE_TOO_LONG = (
    "🎤 That voice note's a bit too long for me to process. Send a shorter one or type it out?"
)
VOICE_FAILED = (
    "🎤 I had trouble processing that voice note. Could you try again, or type your message?"
)


def voice_heard(text: str) -> str:
    """Echo back what the voice note was transcribed to, before acting on it."""
    return f'🎤 Heard: "{text}"'


# --- AI image generation ---
GENERATING_IMAGE = "🎨 Generating your image… give me a moment (this takes ~20–30s)."
REGENERATING_IMAGE = "🎨 Updating the image… one sec."
IMAGE_GEN_FAILED = (
    "🎨 I couldn't generate the image just now, so here's a designed version instead. "
    "Reply *try again* if you'd like me to retry the image."
)
IMAGE_EDIT_FAILED = (
    "🎨 I couldn't update the image just now — your current draft still stands. "
    "Want to reword the change, or reply *approve* / *cancel*?"
)
UNEXPECTED_ERROR = (
    "⚠️ Something went wrong on my side handling that — nothing was changed. "
    "Send it again and I'll have another go."
)
PHOTO_DOWNLOAD_FAILED = (
    "📷 I couldn't download that photo — the rest of your change still went through. "
    "Try sending the picture again?"
)
# A placeholder is a stand-in card, not a photograph of the person. Asking the
# image model to "improve" it invents a face and attaches it to a named
# employee, so the request stops here and asks for the real photo instead.
PLACEHOLDER_NEEDS_PHOTO = (
    "📷 That image is still the placeholder, so there's nothing real for me to edit — "
    "I'd only be inventing a face. Send me the actual photo and I'll drop it straight in."
)
VISUAL_CLARIFY_DEFAULT = (
    "Want this as a designed graphic, or should I generate a photo-style image for it?"
)

# --- VHS video ---
PROCESSING_VIDEO = "🎬 Processing your video… this can take a minute."
VIDEO_FAILED = "🎬 I couldn't process that video — mind resending it, or sending a shorter clip?"


def preview_caption(post: GeneratedPost) -> str:
    """The WhatsApp text sent alongside the preview image."""
    body = f"{post.caption}\n\n{' '.join(post.hashtags)}".strip()
    return f"{body}\n\n— Reply *approve* to publish, send an edit, or *cancel*."


def target_note(platforms: list[plat.Platform] | None) -> str:
    """A line shown on the preview when posting is narrowed to specific platforms."""
    if not platforms or set(platforms) == set(plat.ALL):
        return ""
    return f"\n\n📲 Posting to: {plat.label(platforms)} only."


_PLATFORM_NAMES = {
    plat.Platform.instagram: "Instagram",
    plat.Platform.facebook: "Facebook",
    plat.Platform.linkedin: "LinkedIn",
}


def publish_status(results: dict[Any, Any]) -> str:
    """Build Karen's confirmation, e.g. 'Published: Instagram ✓ · LinkedIn ✗'."""
    if not results:
        return "Nothing to publish."
    parts = []
    for platform, res in results.items():
        name = _PLATFORM_NAMES.get(platform, str(platform))
        if res.success:
            parts.append(f"{name} ✓")
        elif res.error and "not connected" in res.error:
            parts.append(f"{name} — not connected")
        else:
            parts.append(f"{name} ✗ (will retry)")
    return "✅ Approved.\nPublished: " + "  ".join(parts)
