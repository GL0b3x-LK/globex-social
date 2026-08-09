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
from dataclasses import dataclass

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
    if product:
        who.append(
            "The product packaging shown in the other reference photograph must appear "
            "exactly as photographed — same carton, same label, same colours. Never "
            "redesign it or add text to it."
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

    url = await asyncio.to_thread(
        storage.upload_video_asset,
        video_id,
        f"keyframe_{scene.idx}.jpg",
        result.image_bytes,
        "image/jpeg",
    )
    log.info("keyframe ready", extra={"video": video_id, "scene": scene.idx})
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

    done = await asyncio.gather(*(one(s) for s in scenes))
    return {k.scene_idx: k for k in done if k is not None}
