"""The client-facing Google Sheet, as a two-way caption channel.

The 52-week calendar lives in a Google Sheet the client can type into. Its
"Exact Caption" column is a contract in both directions:

* INBOUND — if the client has written anything there, that text is the caption,
  posted VERBATIM. The AI writes the image copy as usual, but the post text is
  not its to write that day.
* OUTBOUND — after a post publishes, the caption that actually went to
  Instagram is written back into the same cell, so the sheet is always a
  truthful record of what ran, written by whoever wrote it.

Access goes through a tiny Apps Script web app bound to the sheet (deployed
once from the sheet's own editor — see scripts/sheet_bridge.gs). That keeps the
production app free of Google credentials: one URL, one shared secret, both in
env. Unconfigured = the whole module is a no-op and the calendar behaves as
before.

Rows are matched by the "Key Feature/Theme" column, which is exactly
``CalendarEntry.title`` — the JSON calendar was generated from this sheet.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.publishing.calendar_sheet")

_TIMEOUT_S = 15.0


def _configured() -> tuple[str, str] | None:
    settings = get_settings()
    url = (settings.sheet_webapp_url or "").strip()
    secret = (settings.sheet_webapp_secret or "").strip()
    return (url, secret) if url and secret else None


async def exact_caption(title: str) -> str | None:
    """The client-authored caption for this calendar entry, or None.

    None means "nothing usable": cell empty, row not found, bridge not
    configured, or bridge unreachable — in every case the AI caption stands.
    """
    conf = _configured()
    if conf is None:
        return None
    url, secret = conf
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(url, params={"secret": secret, "title": title})
            resp.raise_for_status()
            caption = str((resp.json() or {}).get("caption") or "").strip()
            return caption or None
    except Exception as exc:  # noqa: BLE001 — the sheet must never block a draft
        log.warning("sheet caption read failed", extra={"title": title, "error": str(exc)[:120]})
        return None


async def write_back(title: str, caption: str) -> bool:
    """Record the caption that actually published into the sheet row's cell."""
    conf = _configured()
    if conf is None:
        return False
    url, secret = conf
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.post(
                url, json={"secret": secret, "title": title, "caption": caption}
            )
            resp.raise_for_status()
            ok = bool((resp.json() or {}).get("ok"))
            if not ok:
                log.warning("sheet write-back rejected", extra={"title": title})
            return ok
    except Exception as exc:  # noqa: BLE001 — a sheet hiccup must never fail a publish
        log.warning("sheet write-back failed", extra={"title": title, "error": str(exc)[:120]})
        return False
