"""Blotato publishing client (Instagram / Facebook / LinkedIn).

Blotato is async + one-platform-per-call: POST /v2/posts returns a
postSubmissionId, then GET /v2/posts/{id} is polled until published/failed.
Connected accounts are resolved dynamically (GET /v2/users/me/accounts) and
cached — so the system only posts to platforms that are actually connected, and
gains FB/LinkedIn automatically once they're linked in the Blotato dashboard.

Header is `blotato-api-key` sent verbatim (keys can end in '=' — never strip it).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.logging_config import get_logger
from app.publishing.formatter import format_caption
from app.publishing.platforms import Platform

log = get_logger("app.publishing.blotato")

_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 120.0
_MAX_RETRIES = 3


@dataclass(frozen=True)
class PublishResult:
    platform: Platform
    success: bool
    url: str | None = None
    error: str | None = None


class BlotatoClient:
    def __init__(self) -> None:
        self._accounts: dict[str, str] | None = None  # platform -> accountId
        self._lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        return {
            "blotato-api-key": get_settings().blotato_api_key,
            "Content-Type": "application/json",
        }

    async def _request(self, client: httpx.AsyncClient, method: str, path: str, **kw: Any):
        """httpx request retrying transient failures (429 / 5xx / network), not 4xx."""
        last = "unknown error"
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.request(method, path, headers=self._headers(), **kw)
            except httpx.HTTPError as exc:
                last = str(exc)
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"http {resp.status_code}"
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            return resp
        raise RuntimeError(f"blotato {method} {path} failed after retries: {last}")

    async def resolve_accounts(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Map connected platform -> accountId (cached for the process)."""
        if self._accounts is not None:
            return self._accounts
        async with self._lock:
            if self._accounts is None:
                resp = await self._request(client, "GET", "/users/me/accounts")
                items = (resp.json() or {}).get("items") or []
                self._accounts = {
                    str(it["platform"]): str(it["id"]) for it in items if it.get("platform")
                }
                log.info("resolved blotato accounts", extra={"platforms": list(self._accounts)})
        return self._accounts

    async def _page_id(self, client: httpx.AsyncClient, account_id: str) -> str | None:
        try:
            resp = await self._request(
                client, "GET", f"/users/me/accounts/{account_id}/subaccounts"
            )
            items = (resp.json() or {}).get("items") or []
            return str(items[0]["id"]) if items else None
        except Exception as exc:  # noqa: BLE001
            log.warning("blotato subaccount lookup failed", extra={"error": str(exc)})
            return None

    async def _publish_one(
        self,
        client: httpx.AsyncClient,
        platform: Platform,
        account_id: str,
        media_url: str | None,
        text: str,
    ) -> PublishResult:
        target: dict[str, Any] = {"targetType": platform.value}
        if platform is Platform.facebook:
            page_id = await self._page_id(client, account_id)
            if not page_id:
                return PublishResult(platform, False, error="no Facebook page connected")
            target["pageId"] = page_id
        elif platform is Platform.linkedin:
            page_id = await self._page_id(client, account_id)
            if page_id:
                target["pageId"] = page_id  # optional; omit -> personal profile

        body = {
            "post": {
                "accountId": account_id,
                "content": {
                    "text": text,
                    "mediaUrls": [media_url] if media_url else [],
                    "platform": platform.value,
                },
                "target": target,
            }
        }
        try:
            resp = await self._request(client, "POST", "/posts", json=body)
        except Exception as exc:  # noqa: BLE001
            return PublishResult(platform, False, error=str(exc))
        if resp.status_code >= 400:
            return PublishResult(platform, False, error=f"{resp.status_code}: {resp.text[:200]}")
        submission_id = (resp.json() or {}).get("postSubmissionId")
        if not submission_id:
            return PublishResult(platform, False, error="no submission id returned")
        return await self._poll(client, platform, str(submission_id))

    async def _poll(
        self, client: httpx.AsyncClient, platform: Platform, submission_id: str
    ) -> PublishResult:
        waited = 0.0
        while waited < _POLL_TIMEOUT_S:
            await asyncio.sleep(_POLL_INTERVAL_S)
            waited += _POLL_INTERVAL_S
            try:
                resp = await self._request(client, "GET", f"/posts/{submission_id}")
            except Exception:  # noqa: BLE001 — keep polling on transient errors
                continue
            data = resp.json() or {}
            status = data.get("status")
            if status == "published":
                return PublishResult(platform, True, url=data.get("publicUrl"))
            if status == "failed":
                return PublishResult(platform, False, error=data.get("errorMessage") or "failed")
        return PublishResult(platform, False, error="timed out waiting for publish")

    async def publish(
        self,
        media_url: str | None,
        caption: str,
        hashtags: list[str],
        platforms: list[Platform],
    ) -> dict[Platform, PublishResult]:
        """Publish the same post to each requested platform (one call each, in parallel)."""
        async with httpx.AsyncClient(
            base_url=get_settings().blotato_base_url, timeout=30.0
        ) as client:
            try:
                accounts = await self.resolve_accounts(client)
            except Exception as exc:  # noqa: BLE001
                return {
                    p: PublishResult(p, False, error=f"account lookup failed: {exc}")
                    for p in platforms
                }

            async def _one(p: Platform) -> PublishResult:
                account_id = accounts.get(p.value)
                if not account_id:
                    return PublishResult(p, False, error="not connected to Blotato")
                return await self._publish_one(
                    client, p, account_id, media_url, format_caption(caption, hashtags, p)
                )

            results = await asyncio.gather(*(_one(p) for p in platforms))
        return {r.platform: r for r in results}


blotato = BlotatoClient()
