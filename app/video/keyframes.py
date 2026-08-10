"""Scene start frames: the approved face + the real packaging + the scene.

Every scene begins from a locked still. That still is composited from
photographs we already own — the character's stored reference shot and the
product's real pack shot — so identity and packaging are never left to a text
description. The model is only asked to place known things into a setting.

This is the cheap gate: keyframes cost cents, video costs dollars, so the
operator approves what they can see before anything expensive runs.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from PIL import Image

from app.ai import image_gen
from app.db import storage
from app.logging_config import get_logger
from app.video import library
from app.video.models import Scene, SceneKind

log = get_logger("app.video.keyframes")

# Multi-reference model: takes the character photo and the pack shot together
# and preserves both. Verified against our own pack shots before selection.
KEYFRAME_MODEL = "nano-banana-2"
ASPECT = "9:16"

# The generator hands back 7-8 MB stills at its own native resolution, which is
# larger than anything downstream can use — the finished video is 1080x1920.
# Those megabytes are paid for twice: once uploading to storage, and again when
# the generation provider fetches the frame back over the same link. Normalising
# to frame size costs no visible quality and shrinks the file roughly 20x, which
# is the difference between an upload that completes and one that times out.
FRAME_WIDTH, FRAME_HEIGHT = 1080, 1920
_JPEG_QUALITY = 88

_NEGATIVES = (
    "Do not include: any logo, brand mark, wordmark or printed signage that is not "
    "already on the referenced packaging; steam, smoke, haze or floating particles; "
    "dramatic spotlights or lens flare; raw carcasses, blood or graphic meat; "
    "cropped or obscured faces."
)

_STYLE = (
    "Photorealistic documentary still from a working food plant, natural available "
    "light, handheld feel, shallow honest depth of field. Not a studio shot, not a "
    "stock photo."
)


@dataclass(frozen=True)
class Keyframe:
    scene_idx: int
    url: str
    prompt: str


def to_frame_jpeg(data: bytes) -> bytes:
    """Shrink a generated still to frame size as a JPEG.

    Only ever downscales, so a frame that is already small is left alone. If the
    bytes cannot be decoded the original is returned untouched — a frame we paid
    for is worth more than a tidy file size.
    """
    try:
        opened = Image.open(io.BytesIO(data))
        image = opened.convert("RGB") if opened.mode != "RGB" else opened
        image.thumbnail((FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=_JPEG_QUALITY, optimize=True)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — never lose a paid-for frame to a resize
        log.warning("keyframe downscale failed; using original", extra={"error": str(exc)[:120]})
        return data


def build_prompt(
    scene: Scene,
    character: library.Character | None,
    product: library.Product | None,
) -> str:
    """Compose the instruction. Identity and packaging come from the references."""
    who = []
    if character and scene.kind is SceneKind.speaking:
        who.append(
            f"The person in the first reference image, {character.role}, is the subject. "
            "Keep their face, build and clothing exactly as in the reference."
        )
    elif character:
        who.append(
            "The person in the first reference image may appear, seen at work rather "
            "than addressing the camera. Keep their face and clothing as referenced."
        )
    if product and product.hero_files:
        who.append(
            "The product packaging shown in the other reference photograph must appear "
            "exactly as photographed — same carton, same label, same colours. Never "
            "redesign it or add text to it."
        )
    elif product:
        # No photograph of this line's packaging exists, so the sentence above
        # would be pointing at nothing — which is an invitation to invent a
        # Globex carton. Most of the catalogue is in this state.
        who.append(
            "We hold NO photograph of this product's packaging, so no Globex packaging "
            "may appear. Show the product bare, or in plain unbranded packaging with "
            "nothing printed on it. Never invent a Globex logo, label, carton or text."
        )
    return "\n".join(
        [
            f"{scene.keyframe_prompt}",
            f"Setting: {scene.setting}. Camera: {scene.camera}. Action: {scene.action}",
            *who,
            f"Style: {_STYLE}",
            _NEGATIVES,
        ]
    )


def reference_urls(
    scene: Scene,
    character: library.Character | None,
    product: library.Product | None,
) -> list[str]:
    """Which stored photographs this scene is built from, identity first."""
    urls: list[str] = []
    if character and character.reference_image_urls:
        # Speaking scenes use the front portrait (identity anchor); cutaways use
        # the working-context shot so the pose suits the action.
        wanted = "front" if scene.kind is SceneKind.speaking else "context"
        match = [u for u in character.reference_image_urls if u.endswith(f"{wanted}.jpg")]
        urls.append(match[0] if match else character.reference_image_urls[0])
    if product:
        pack = product.hero_files
        if pack:
            urls.append(storage.public_url(f"products/{product.slug}/{pack[0]}"))
    return urls


async def render_scene(
    video_id: str,
    scene: Scene,
    character: library.Character | None,
    product: library.Product | None,
) -> Keyframe | None:
    """Composite one scene's start frame and host it. Returns None on failure."""
    prompt = build_prompt(scene, character, product)
    refs = reference_urls(scene, character, product)

    result = (
        await image_gen.edit_multi(refs, prompt, aspect_ratio=ASPECT, model=KEYFRAME_MODEL)
        if refs
        else await image_gen.generate(prompt, aspect_ratio=ASPECT, model=KEYFRAME_MODEL)
    )
    if not result.ok or not result.image_bytes:
        log.error(
            "keyframe failed",
            extra={"video": video_id, "scene": scene.idx, "error": result.error},
        )
        return None

    payload = to_frame_jpeg(result.image_bytes)
    try:
        url = await asyncio.to_thread(
            storage.upload_video_asset,
            video_id,
            f"keyframe_{scene.idx}.jpg",
            payload,
            "image/jpeg",
        )
    except Exception as exc:  # noqa: BLE001 — one bad upload must not abort the batch
        log.error(
            "keyframe upload failed",
            extra={"video": video_id, "scene": scene.idx, "error": str(exc)[:160]},
        )
        return None
    log.info(
        "keyframe ready",
        extra={"video": video_id, "scene": scene.idx, "kb": len(payload) // 1024},
    )
    return Keyframe(scene_idx=scene.idx, url=url, prompt=prompt)


async def render_all(
    video_id: str,
    scenes: list[Scene],
    character: library.Character | None,
    product: library.Product | None,
    *,
    concurrency: int = 3,
) -> dict[int, Keyframe]:
    """Composite every scene's start frame. Missing scenes are simply absent."""
    sem = asyncio.Semaphore(concurrency)

    async def one(scene: Scene) -> Keyframe | None:
        async with sem:
            return await render_scene(video_id, scene, character, product)

    # return_exceptions: a scene that blows up is a missing scene, not a dead
    # video. produce() already handles an incomplete frame set, and by this point
    # the generation has been paid for — losing the rest to it would be waste.
    done = await asyncio.gather(*(one(s) for s in scenes), return_exceptions=True)
    for scene, outcome in zip(scenes, done, strict=True):
        if isinstance(outcome, BaseException):
            log.error(
                "keyframe errored",
                extra={"video": video_id, "scene": scene.idx, "error": str(outcome)[:160]},
            )
    return {k.scene_idx: k for k in done if isinstance(k, Keyframe)}
