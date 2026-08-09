"""AI image generation via kie.ai (nano-banana).

The generated image is only ever the photographic layer that the brand template
overlays on top of (see CLAUDE.md) — kie.ai supplies the scene, never the logo or
brand colours. Used by the on-demand flow when Karen asks for an image post.

kie.ai is async: create a task, poll until it succeeds, then download the hosted
result (URLs are short-lived, so we fetch the bytes immediately and re-host).
`generate()` (text→image) and `edit()` (image→image) both return an `ImageResult`
and never raise into the caller — any failure becomes `ok=False` so the handler
can fall back to a designed typographic post instead of leaving Karen hanging.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.ai.image_gen")

_CREATE_PATH = "/api/v1/jobs/createTask"
_RECORD_PATH = "/api/v1/jobs/recordInfo"

# kie.ai generation is slow (~15–30s observed); poll patiently before giving up.
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 150.0
_CREATE_RETRIES = 3  # for HTTP 429 (rate limited — the request does not enter the queue)

# Models that take their input images under "image_input" (vs "image_urls").
_IMAGE_INPUT_MODELS = ("nano-banana-2", "nano-banana-pro")

# GPT Image models take `resolution` (1K/2K/4K) and reject `output_format`.
_GPT_IMAGE_MODELS = ("gpt-image-",)


@dataclass(frozen=True)
class ImageResult:
    ok: bool
    image_bytes: bytes | None = None
    error: str | None = None


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _edit_field(model: str) -> str:
    return "image_input" if model.startswith(_IMAGE_INPUT_MODELS) else "image_urls"


def _generate_input(model: str, prompt: str, aspect_ratio: str, resolution: str) -> dict[str, str]:
    """Each kie.ai model family accepts a different input shape — send only its own.

    An unknown key is rejected at task creation, so this cannot be one shared dict.
    """
    if model.startswith(_GPT_IMAGE_MODELS):
        return {"prompt": prompt, "aspect_ratio": aspect_ratio, "resolution": resolution}
    return {"prompt": prompt, "aspect_ratio": aspect_ratio, "output_format": "png"}


async def _create_task(client: httpx.AsyncClient, key: str, body: dict) -> tuple[str | None, str]:
    """Submit a generation task. Returns (taskId, "") or (None, error)."""
    for attempt in range(_CREATE_RETRIES):
        resp = await client.post(_CREATE_PATH, headers=_headers(key), json=body)
        if resp.status_code == 429:
            await asyncio.sleep(2.0 * (attempt + 1))
            continue
        try:
            data = resp.json()
        except ValueError:
            return None, f"http {resp.status_code}"
        # kie.ai often returns HTTP 200 with an error code in the body — check the body.
        if data.get("code") != 200:
            return None, f"create code={data.get('code')} msg={data.get('msg')}"
        task_id = (data.get("data") or {}).get("taskId")
        return (task_id, "") if task_id else (None, "no taskId in response")
    return None, "rate limited (429)"


async def _poll(client: httpx.AsyncClient, key: str, task_id: str) -> tuple[str | None, str]:
    """Poll until the task finishes. Returns (result_url, "") or (None, error)."""
    waited = 0.0
    while waited < _POLL_TIMEOUT_S:
        await asyncio.sleep(_POLL_INTERVAL_S)
        waited += _POLL_INTERVAL_S
        resp = await client.get(_RECORD_PATH, headers=_headers(key), params={"taskId": task_id})
        try:
            data = (resp.json() or {}).get("data") or {}
        except ValueError:
            continue
        state = data.get("state")
        if state == "success":
            try:
                urls = json.loads(data.get("resultJson") or "{}").get("resultUrls") or []
            except (ValueError, TypeError):
                return None, "could not parse resultJson"
            return (urls[0], "") if urls else (None, "success but no result URL")
        if state == "fail":
            return None, f"{data.get('failCode')}: {data.get('failMsg')}"
        # waiting / queuing / generating → keep polling
    return None, f"timed out after {int(_POLL_TIMEOUT_S)}s"


async def download(url: str) -> bytes:
    """Fetch image bytes from a (public) URL. Raises on HTTP error."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _run(body: dict, *, label: str) -> ImageResult:
    settings = get_settings()
    if not settings.kie_api_key:
        log.error("image generation requested but KIE_API_KEY is not configured")
        return ImageResult(ok=False, error="no_key")
    key = settings.kie_api_key
    try:
        async with httpx.AsyncClient(base_url=settings.kie_base_url, timeout=30.0) as client:
            task_id, err = await _create_task(client, key, body)
            if not task_id:
                log.error("kie.ai createTask failed", extra={"label": label, "error": err})
                return ImageResult(ok=False, error=err)
            url, err = await _poll(client, key, task_id)
            if not url:
                log.error(
                    "kie.ai task did not produce an image", extra={"label": label, "error": err}
                )
                return ImageResult(ok=False, error=err)
        image_bytes = await download(url)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — any failure must degrade, not crash
        log.error("kie.ai image step errored", extra={"label": label, "error": str(exc)})
        return ImageResult(ok=False, error=str(exc))
    log.info("kie.ai image ready", extra={"label": label, "bytes": len(image_bytes)})
    return ImageResult(ok=True, image_bytes=image_bytes)


async def generate(
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    model: str | None = None,
    resolution: str = "2K",
) -> ImageResult:
    """Text→image. Returns an ImageResult; never raises.

    `model` overrides the configured default — used by the video engine's
    character sheets, which run on GPT Image rather than the post pipeline's model.
    """
    chosen = model or get_settings().kie_image_model
    body = {
        "model": chosen,
        "input": _generate_input(chosen, prompt, aspect_ratio, resolution),
    }
    return await _run(body, label="generate")


async def edit(image_url: str, prompt: str, *, aspect_ratio: str = "1:1") -> ImageResult:
    """Image→image (img2img): apply `prompt` as a change to the image at `image_url`."""
    settings = get_settings()
    model = settings.kie_edit_model
    body = {
        "model": model,
        "input": {
            "prompt": prompt,
            _edit_field(model): [image_url],
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
        },
    }
    return await _run(body, label="edit")
