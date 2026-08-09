"""Video generation: locked keyframe (+ voice track) -> a clip.

Two kinds of scene, two models:

* **speaking** — Kling AI Avatar takes the keyframe and the character's actual
  voice track and lip-syncs to it. Audio-first means the clip is exactly as long
  as the words, so picture and sound can never drift apart.
* **broll** — Kling image-to-video animates the keyframe with gentle motion.

The provider interface exists so the engine is never hostage to one vendor: the
same two calls can be served by Higgsfield (whose Cloud API hosts these models
too) by adding a second implementation, with no change upstream.

Generation is the only expensive step, so a clip is never made twice for the
same inputs — the caller keys artifacts by content hash.
"""

from __future__ import annotations

import abc
import asyncio
import json
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.video.providers")

_CREATE_PATH = "/api/v1/jobs/createTask"
_RECORD_PATH = "/api/v1/jobs/recordInfo"

# Video generation is slow — minutes, not seconds.
_POLL_INTERVAL_S = 6.0
_POLL_TIMEOUT_S = 900.0
_CREATE_RETRIES = 3

SPEAKING_MODEL = "kling/ai-avatar-standard"
BROLL_MODEL = "kling/v3-turbo-image-to-video"
RESOLUTION = "1080p"


@dataclass(frozen=True)
class ClipResult:
    ok: bool
    url: str | None = None  # provider-hosted URL (short-lived — download promptly)
    error: str | None = None


class VideoGenProvider(abc.ABC):
    """What the engine needs from any generation vendor."""

    @abc.abstractmethod
    async def speaking_scene(self, keyframe_url: str, audio_url: str, prompt: str) -> ClipResult:
        """Lip-sync the person in `keyframe_url` to `audio_url`."""

    @abc.abstractmethod
    async def broll_scene(self, keyframe_url: str, prompt: str, seconds: float) -> ClipResult:
        """Animate `keyframe_url` with gentle motion for roughly `seconds`."""


class KieProvider(VideoGenProvider):
    """kie.ai — hosts Kling's avatar and image-to-video models under one key."""

    def _headers(self) -> dict[str, str]:
        key = get_settings().kie_api_key or ""
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def _create(self, client: httpx.AsyncClient, body: dict) -> tuple[str | None, str]:
        for attempt in range(_CREATE_RETRIES):
            try:
                resp = await client.post(_CREATE_PATH, headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                await asyncio.sleep(2.0 * (attempt + 1))
                if attempt == _CREATE_RETRIES - 1:
                    return None, str(exc)
                continue
            if resp.status_code == 429:
                await asyncio.sleep(3.0 * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError:
                return None, f"http {resp.status_code}"
            if data.get("code") != 200:
                return None, f"code={data.get('code')} msg={data.get('msg')}"
            task_id = (data.get("data") or {}).get("taskId")
            return (task_id, "") if task_id else (None, "no taskId")
        return None, "rate limited"

    async def _poll(self, client: httpx.AsyncClient, task_id: str) -> tuple[str | None, str]:
        waited = 0.0
        while waited < _POLL_TIMEOUT_S:
            await asyncio.sleep(_POLL_INTERVAL_S)
            waited += _POLL_INTERVAL_S
            try:
                resp = await client.get(
                    _RECORD_PATH, headers=self._headers(), params={"taskId": task_id}
                )
                data = (resp.json() or {}).get("data") or {}
            except (httpx.HTTPError, ValueError):
                continue  # transient — keep waiting
            state = data.get("state")
            if state == "success":
                try:
                    urls = json.loads(data.get("resultJson") or "{}").get("resultUrls") or []
                except (ValueError, TypeError):
                    return None, "unparseable result"
                return (urls[0], "") if urls else (None, "no result url")
            if state == "fail":
                return None, f"{data.get('failCode')}: {data.get('failMsg')}"
        return None, f"timed out after {int(_POLL_TIMEOUT_S)}s"

    async def _run(self, body: dict, *, label: str) -> ClipResult:
        settings = get_settings()
        if not settings.kie_api_key:
            return ClipResult(ok=False, error="no_key")
        try:
            async with httpx.AsyncClient(base_url=settings.kie_base_url, timeout=60.0) as client:
                task_id, err = await self._create(client, body)
                if not task_id:
                    log.error("clip create failed", extra={"label": label, "error": err})
                    return ClipResult(ok=False, error=err)
                url, err = await self._poll(client, task_id)
                if not url:
                    log.error("clip generation failed", extra={"label": label, "error": err})
                    return ClipResult(ok=False, error=err)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a provider fault must not crash the job
            log.error("clip step errored", extra={"label": label, "error": str(exc)})
            return ClipResult(ok=False, error=str(exc))
        log.info("clip ready", extra={"label": label})
        return ClipResult(ok=True, url=url)

    async def speaking_scene(self, keyframe_url: str, audio_url: str, prompt: str) -> ClipResult:
        return await self._run(
            {
                "model": SPEAKING_MODEL,
                "input": {
                    "image_url": keyframe_url,
                    "audio_url": audio_url,
                    "prompt": prompt[:5000],
                },
            },
            label="speaking",
        )

    async def broll_scene(self, keyframe_url: str, prompt: str, seconds: float) -> ClipResult:
        return await self._run(
            {
                "model": BROLL_MODEL,
                "input": {
                    "image_urls": [keyframe_url],
                    "prompt": prompt[:2000],
                    "duration": duration_bucket(seconds),
                    "resolution": RESOLUTION,
                },
            },
            label="broll",
        )


def duration_bucket(seconds: float) -> str:
    """Providers sell fixed clip lengths; pick the shortest that covers the scene.

    Overshooting is free to fix (assembly trims), undershooting would leave a gap.
    """
    return "5" if seconds <= 5.5 else "10"


async def download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def get_provider() -> VideoGenProvider:
    """The configured backend. One place to swap vendors."""
    return KieProvider()
