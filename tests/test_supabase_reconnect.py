"""A Supabase connection the server closed while idle must be retried on a
fresh one, not surfaced as a failed request.

On Railway the app holds one long-lived HTTP/2 connection to Supabase. After
a quiet spell the server sends GOAWAY and the next request died with
`httpx.RemoteProtocolError: ConnectionTerminated` — the client review page
answered 503 on every save, and the re-delivery job failed in the same
minute. A fresh process never sees it, which is why local runs all passed.
"""

from __future__ import annotations

import httpx
import pytest

from app.db import client as db_client


def _transport_that_fails_first(
    times: int, message: str
) -> tuple[db_client.ReconnectingTransport, list]:
    calls: list[str] = []
    state = {"left": times}

    def fake_handle(self, request):
        calls.append(request.method)
        if state["left"] > 0:
            state["left"] -= 1
            raise httpx.RemoteProtocolError(message)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = db_client.ReconnectingTransport()
    # Patch the parent's handler on this instance only.
    transport._parent_handle = fake_handle.__get__(transport)  # type: ignore[attr-defined]
    return transport, calls


def test_a_terminated_connection_is_retried_once_even_for_a_write() -> None:
    transport, calls = _transport_that_fails_first(
        1, "<ConnectionTerminated error_code:0, last_stream_id:3, additional_data:None>"
    )
    resp = transport.handle_request(httpx.Request("POST", "https://x.test/rest/v1/post_feedback"))
    assert resp.status_code == 200
    assert calls == ["POST", "POST"]


def test_a_read_is_retried_whatever_the_disconnect_looked_like() -> None:
    transport, calls = _transport_that_fails_first(
        1, "Server disconnected without sending a response."
    )
    resp = transport.handle_request(httpx.Request("GET", "https://x.test/rest/v1/posts"))
    assert resp.status_code == 200 and calls == ["GET", "GET"]


def test_an_ambiguous_disconnect_on_a_write_is_not_replayed() -> None:
    """A write the server may already have applied is not sent twice."""
    transport, calls = _transport_that_fails_first(
        1, "Server disconnected without sending a response."
    )
    with pytest.raises(httpx.RemoteProtocolError):
        transport.handle_request(httpx.Request("POST", "https://x.test/rest/v1/posts"))
    assert calls == ["POST"]


def test_a_second_failure_is_surfaced_not_looped() -> None:
    transport, calls = _transport_that_fails_first(
        2, "<ConnectionTerminated error_code:0, last_stream_id:3, additional_data:None>"
    )
    with pytest.raises(httpx.RemoteProtocolError):
        transport.handle_request(httpx.Request("GET", "https://x.test/rest/v1/posts"))
    assert calls == ["GET", "GET"]
