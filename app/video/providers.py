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
    """The configured backend. One place to swap vendors.

    Defaults to Higgsfield when its credentials are present — it is the client's
    own account and the tool the approved reference video came from — and falls
    back to kie.ai otherwise so the pipeline is never dead in the water.
    """
    name = (get_settings().video_provider or "").lower()
    if name == "kie":
        return KieProvider()
    if name == "higgsfield":
        return HiggsfieldProvider()
    settings = get_settings()
    if settings.higgsfield_api_key and settings.higgsfield_api_secret:
        return HiggsfieldProvider()
    return KieProvider()


def _friendly_error(resp: httpx.Response) -> str:
    """Turn a vendor error into something the operator can actually act on."""
    try:
        detail = (resp.json() or {}).get("detail")
    except ValueError:
        detail = None
    if detail == "not_enough_credits":
        return (
            "the Higgsfield account is out of credits — top it up at "
            "cloud.higgsfield.ai and try again (nothing was charged)"
        )
    if detail == "model_not_found":
        return "that Higgsfield model is not available on this account"
    return f"{resp.status_code}: {detail or resp.text[:160]}"


class HiggsfieldProvider(VideoGenProvider):
    """Higgsfield Cloud API — the client's own account and the tool the approved
    reference video was made with.

    Same async shape as everything else: POST the model id, poll the request,
    take the URL. Notable differences from kie: auth is ``Key <key>:<secret>``
    (not Bearer), failed and NSFW generations are refunded automatically, and
    generated files are retained for as little as 7 days — so the caller must
    take ownership of the bytes promptly, which it already does.

    Inputs are supplied as public URLs, so the character reference shots hosted
    in Supabase are usable directly; nothing needs uploading into Higgsfield.
    """

    BASE_URL = "https://platform.higgsfield.ai"

    # Verified against the live account's /models list on 2026-08-09.
    # DoP is Higgsfield's own motion model and is what gave the approved
    # reference video its look; Speak 2.0 does the lip-sync and takes exactly
    # the same three inputs our pipeline already produces.
    BROLL_MODEL = "higgsfield-ai/dop/standard"  # 9 credits; lite=2, turbo=6.5
    SPEAKING_MODEL = "higgsfield-ai/speak"
    AUDIO_PARAM = "audio_url"

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        key, secret = settings.higgsfield_api_key, settings.higgsfield_api_secret
        return {
            "Authorization": f"Key {key}:{secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _configured(self) -> bool:
        settings = get_settings()
        return bool(settings.higgsfield_api_key and settings.higgsfield_api_secret)

    async def _submit(self, model_id: str, payload: dict, *, label: str) -> ClipResult:
        if not self._configured():
            return ClipResult(ok=False, error="higgsfield credentials not configured")
        try:
            async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=60.0) as client:
                resp = await client.post(f"/{model_id}", headers=self._headers(), json=payload)
                if resp.status_code >= 400:
                    return ClipResult(ok=False, error=_friendly_error(resp))
                body = resp.json() or {}
                request_id = body.get("request_id")
                if not request_id:
                    return ClipResult(ok=False, error="no request_id returned")
                return await self._poll(client, str(request_id), label=label)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a vendor fault must not crash the job
            log.error("higgsfield submit failed", extra={"label": label, "error": str(exc)})
            return ClipResult(ok=False, error=str(exc))

    async def _poll(self, client: httpx.AsyncClient, request_id: str, *, label: str) -> ClipResult:
        waited = 0.0
        while waited < _POLL_TIMEOUT_S:
            await asyncio.sleep(_POLL_INTERVAL_S)
            waited += _POLL_INTERVAL_S
            try:
                resp = await client.get(f"/requests/{request_id}/status", headers=self._headers())
                data = resp.json() or {}
            except (httpx.HTTPError, ValueError):
                continue  # transient — keep waiting
            status = data.get("status")
            if status == "completed":
                url = (data.get("video") or {}).get("url")
                if not url:
                    images = data.get("images") or []
                    url = images[0].get("url") if images else None
                return (
                    ClipResult(ok=True, url=str(url))
                    if url
                    else ClipResult(ok=False, error="completed without media")
                )
            if status in ("failed", "nsfw"):
                # Higgsfield refunds these, so a retry costs nothing but time.
                return ClipResult(ok=False, error=f"{status}: {data.get('error') or ''}"[:200])
        log.error("higgsfield timed out", extra={"label": label, "request": request_id})
        return ClipResult(ok=False, error=f"timed out after {int(_POLL_TIMEOUT_S)}s")

    async def speaking_scene(self, keyframe_url: str, audio_url: str, prompt: str) -> ClipResult:
        """Lip-synced scene via Speak 2.0 — image + our own voice track + prompt."""
        settings = get_settings()
        model_id = settings.higgsfield_speaking_model or self.SPEAKING_MODEL
        audio_param = settings.higgsfield_audio_param or self.AUDIO_PARAM
        return await self._submit(
            model_id,
            {"image_url": keyframe_url, audio_param: audio_url, "prompt": prompt[:2000]},
            label="hf-speaking",
        )

    async def broll_scene(self, keyframe_url: str, prompt: str, seconds: float) -> ClipResult:
        model_id = get_settings().higgsfield_broll_model or self.BROLL_MODEL
        return await self._submit(
            model_id,
            {
                "image_url": keyframe_url,
                "prompt": prompt[:2000],
                "duration": int(duration_bucket(seconds)),
            },
            label="hf-broll",
        )
