"""Blotato client tests — HTTP mocked with respx (no network).

Covers the create→poll happy path, not-connected platforms, partial failure,
a 'failed' status, retry on 503, and no-retry on 401.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

import app.publishing.blotato as b
from app.publishing.blotato import BlotatoClient
from app.publishing.platforms import Platform

BASE = "https://backend.blotato.com/v2"
_IG_ACCOUNTS = {"items": [{"id": "51165", "platform": "instagram"}]}


@pytest.fixture(autouse=True)
def _fast_settings(monkeypatch):
    monkeypatch.setattr(
        b, "get_settings", lambda: SimpleNamespace(blotato_api_key="k", blotato_base_url=BASE)
    )
    monkeypatch.setattr(b, "_POLL_INTERVAL_S", 0.0)

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(b.asyncio, "sleep", _no_sleep)


@respx.mock
async def test_publish_instagram_success() -> None:
    respx.get(f"{BASE}/users/me/accounts").mock(return_value=httpx.Response(200, json=_IG_ACCOUNTS))
    respx.post(f"{BASE}/posts").mock(
        return_value=httpx.Response(200, json={"postSubmissionId": "sub1"})
    )
    respx.get(f"{BASE}/posts/sub1").mock(
        return_value=httpx.Response(200, json={"status": "published", "publicUrl": "https://ig/p"})
    )
    res = await BlotatoClient().publish("https://img/x.png", "hi", ["#g"], [Platform.instagram])
    assert res[Platform.instagram].success
    assert res[Platform.instagram].url == "https://ig/p"


@respx.mock
async def test_unconnected_platform_is_reported() -> None:
    respx.get(f"{BASE}/users/me/accounts").mock(return_value=httpx.Response(200, json=_IG_ACCOUNTS))
    res = await BlotatoClient().publish("u", "c", [], [Platform.linkedin])
    assert not res[Platform.linkedin].success
    assert "not connected" in (res[Platform.linkedin].error or "")


@respx.mock
async def test_partial_success_ig_ok_linkedin_unconnected() -> None:
    respx.get(f"{BASE}/users/me/accounts").mock(return_value=httpx.Response(200, json=_IG_ACCOUNTS))
    respx.post(f"{BASE}/posts").mock(
        return_value=httpx.Response(200, json={"postSubmissionId": "s"})
    )
    respx.get(f"{BASE}/posts/s").mock(
        return_value=httpx.Response(200, json={"status": "published", "publicUrl": "u"})
    )
    res = await BlotatoClient().publish("u", "c", [], [Platform.instagram, Platform.linkedin])
    assert res[Platform.instagram].success
    assert not res[Platform.linkedin].success


@respx.mock
async def test_failed_status_is_surfaced() -> None:
    respx.get(f"{BASE}/users/me/accounts").mock(return_value=httpx.Response(200, json=_IG_ACCOUNTS))
    respx.post(f"{BASE}/posts").mock(
        return_value=httpx.Response(200, json={"postSubmissionId": "s"})
    )
    respx.get(f"{BASE}/posts/s").mock(
        return_value=httpx.Response(200, json={"status": "failed", "errorMessage": "bad media"})
    )
    res = await BlotatoClient().publish("u", "c", [], [Platform.instagram])
    assert not res[Platform.instagram].success
    assert "bad media" in (res[Platform.instagram].error or "")


@respx.mock
async def test_retry_on_503_then_succeeds() -> None:
    respx.get(f"{BASE}/users/me/accounts").mock(return_value=httpx.Response(200, json=_IG_ACCOUNTS))
    route = respx.post(f"{BASE}/posts").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"postSubmissionId": "s"})]
    )
    respx.get(f"{BASE}/posts/s").mock(
        return_value=httpx.Response(200, json={"status": "published", "publicUrl": "u"})
    )
    res = await BlotatoClient().publish("u", "c", [], [Platform.instagram])
    assert res[Platform.instagram].success
    assert route.call_count == 2  # retried the 503


@respx.mock
async def test_no_retry_on_401() -> None:
    respx.get(f"{BASE}/users/me/accounts").mock(return_value=httpx.Response(200, json=_IG_ACCOUNTS))
    route = respx.post(f"{BASE}/posts").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    res = await BlotatoClient().publish("u", "c", [], [Platform.instagram])
    assert not res[Platform.instagram].success
    assert route.call_count == 1  # 4xx is not retried
