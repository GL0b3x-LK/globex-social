"""kie.ai image-generation tests — fully offline (httpx is faked).

Covers the async create→poll→download flow and every way it can fail: a missing
key, an error code in the body, a 429 retry, a poll 'fail' state, and a poll
timeout. None may raise — each resolves to an ImageResult the handler can branch on.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai import image_gen


class _Resp:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    """Scripted httpx.AsyncClient: routes post→create, get→poll/download."""

    def __init__(self, script):
        self._s = script
        self._poll_i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, path, **kw):
        self._s.setdefault("bodies", []).append(kw.get("json"))
        create = self._s["create"]
        return create.pop(0) if isinstance(create, list) else create

    async def get(self, path, **kw):
        if "recordInfo" in path:
            polls = self._s["poll"]
            resp = polls[min(self._poll_i, len(polls) - 1)]
            self._poll_i += 1
            return resp
        return self._s.get("download", _Resp(content=b"PNGBYTES"))


def _ok_poll(url="https://img.kie/result.png"):
    return _Resp(
        json_data={"data": {"state": "success", "resultJson": json.dumps({"resultUrls": [url]})}}
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(
        image_gen,
        "get_settings",
        lambda: SimpleNamespace(
            kie_api_key="k",
            kie_base_url="https://api.kie.ai",
            kie_image_model="nano-banana-2",
            kie_edit_model="nano-banana-2",
        ),
    )

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(image_gen.asyncio, "sleep", _no_sleep)


def _patch_client(monkeypatch, script):
    monkeypatch.setattr(image_gen.httpx, "AsyncClient", lambda *a, **k: _FakeClient(script))
    return script


async def test_generate_success(monkeypatch) -> None:
    _patch_client(
        monkeypatch,
        {
            "create": _Resp(json_data={"code": 200, "data": {"taskId": "t1"}}),
            "poll": [_Resp(json_data={"data": {"state": "generating"}}), _ok_poll()],
            "download": _Resp(content=b"PNGBYTES"),
        },
    )
    result = await image_gen.generate("a port at dawn")
    assert result.ok
    assert result.image_bytes == b"PNGBYTES"


async def test_missing_key_is_not_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        image_gen,
        "get_settings",
        lambda: SimpleNamespace(
            kie_api_key=None, kie_base_url="x", kie_image_model="m", kie_edit_model="m"
        ),
    )
    result = await image_gen.generate("anything")
    assert not result.ok
    assert result.error == "no_key"


async def test_create_error_code_is_not_ok(monkeypatch) -> None:
    _patch_client(
        monkeypatch,
        {"create": _Resp(json_data={"code": 402, "msg": "insufficient credits"})},
    )
    result = await image_gen.generate("a port")
    assert not result.ok
    assert "402" in (result.error or "")


async def test_429_is_retried_then_succeeds(monkeypatch) -> None:
    _patch_client(
        monkeypatch,
        {
            "create": [
                _Resp(status_code=429),
                _Resp(json_data={"code": 200, "data": {"taskId": "t1"}}),
            ],
            "poll": [_ok_poll()],
            "download": _Resp(content=b"PNGBYTES"),
        },
    )
    result = await image_gen.generate("a port")
    assert result.ok


async def test_poll_fail_state_is_not_ok(monkeypatch) -> None:
    _patch_client(
        monkeypatch,
        {
            "create": _Resp(json_data={"code": 200, "data": {"taskId": "t1"}}),
            "poll": [
                _Resp(json_data={"data": {"state": "fail", "failCode": "501", "failMsg": "boom"}})
            ],
        },
    )
    result = await image_gen.generate("a port")
    assert not result.ok
    assert "boom" in (result.error or "")


async def test_poll_timeout_is_not_ok(monkeypatch) -> None:
    monkeypatch.setattr(image_gen, "_POLL_TIMEOUT_S", 3.0)
    monkeypatch.setattr(image_gen, "_POLL_INTERVAL_S", 1.0)
    _patch_client(
        monkeypatch,
        {
            "create": _Resp(json_data={"code": 200, "data": {"taskId": "t1"}}),
            "poll": [_Resp(json_data={"data": {"state": "generating"}})],  # never finishes
        },
    )
    result = await image_gen.generate("a port")
    assert not result.ok
    assert "timed out" in (result.error or "")


async def test_edit_uses_image_input_field_for_nano_banana_2(monkeypatch) -> None:
    script = _patch_client(
        monkeypatch,
        {
            "create": _Resp(json_data={"code": 200, "data": {"taskId": "t1"}}),
            "poll": [_ok_poll()],
            "download": _Resp(content=b"PNGBYTES"),
        },
    )
    result = await image_gen.edit("https://img.kie/raw.png", "make it sunset")
    assert result.ok
    sent = script["bodies"][0]["input"]
    assert sent["image_input"] == ["https://img.kie/raw.png"]
    assert "image_urls" not in sent


def test_edit_field_mapping() -> None:
    assert image_gen._edit_field("nano-banana-2") == "image_input"
    assert image_gen._edit_field("nano-banana-pro") == "image_input"
    assert image_gen._edit_field("google/nano-banana-edit") == "image_urls"
