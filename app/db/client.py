"""Supabase client singleton + response unwrap helpers.

Server-side uses the service_role key (SUPABASE_KEY), which bypasses RLS. The
unwrap helpers narrow postgrest's loosely-typed ``response.data`` (``list[JSON]``)
to the row dicts our helpers actually return — one place to cast, not 28.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

import httpx
from supabase import Client, ClientOptions, create_client

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.db.client")
Row = dict[str, Any]


# Video masters are tens of megabytes; the storage client's default timeout is
# sized for images and cuts an upload off mid-flight.
_STORAGE_TIMEOUT_S = 600


# Connecting is quick or it is not happening; the long budget is for reads and
# uploads (video masters) and is shared by every Supabase sub-client because a
# supplied httpx client carries its own timeout, not the per-service ones.
_TIMEOUT = httpx.Timeout(_STORAGE_TIMEOUT_S, connect=10.0)


def _safe_to_replay(request: httpx.Request, exc: httpx.RemoteProtocolError) -> bool:
    """May this request be sent again after the connection failed?

    An HTTP/2 GOAWAY ("ConnectionTerminated") means the server closed the
    connection before taking our stream: nothing was processed, so anything
    can be replayed. Any other mid-flight disconnect is ambiguous for a write —
    it may already have landed — so only reads are replayed then.
    """
    return "ConnectionTerminated" in str(exc) or request.method in ("GET", "HEAD")


class ReconnectingTransport(httpx.HTTPTransport):
    """Retry once when the server has closed the pooled connection under us.

    The app holds one long-lived HTTP/2 connection to Supabase. After a quiet
    spell Supabase closes it; httpx then failed the NEXT request with
    RemoteProtocolError instead of opening a fresh connection — every save on
    the client review page answered 503, and the re-delivery job failed in the
    same minute (2026-09-11). The pool drops the dead connection on the error,
    so one retry lands on a new one.
    """

    def _parent_handle(self, request: httpx.Request) -> httpx.Response:
        return super().handle_request(request)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._parent_handle(request)
        except httpx.RemoteProtocolError as exc:
            if not _safe_to_replay(request, exc):
                raise
            log.warning(
                "supabase closed the connection; retrying on a fresh one",
                extra={"method": request.method, "path": request.url.path, "error": str(exc)[:120]},
            )
            return self._parent_handle(request)


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    # postgrest and storage3 both build absolute URLs and pass their own headers
    # per request, so the shared client needs neither a base URL nor defaults.
    http = httpx.Client(
        transport=ReconnectingTransport(http2=True),
        timeout=_TIMEOUT,
        follow_redirects=True,
    )
    return create_client(
        settings.supabase_url,
        settings.supabase_key,
        options=ClientOptions(httpx_client=http, storage_client_timeout=_STORAGE_TIMEOUT_S),
    )


def rows(resp: Any) -> list[Row]:
    """All rows from a postgrest response."""
    return cast("list[Row]", resp.data)


def maybe_row(resp: Any) -> Row | None:
    """First row, or None when the result set is empty."""
    data = resp.data
    return cast("Row", data[0]) if data else None


def row(resp: Any) -> Row:
    """First row from a write that returns its representation (insert/update/upsert)."""
    return cast("Row", resp.data[0])


def ping() -> bool:
    """Lightweight connectivity check for /health. Raises on failure."""
    get_supabase().table("employees").select("id").limit(1).execute()
    return True
