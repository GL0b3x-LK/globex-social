"""The 24-hour service window, and the template that survives it.

Three days of scheduled posts were generated, rendered, stored and never seen:
WhatsApp only carries a free-form business message inside 24 hours of the
recipient's last inbound one, and outside it Twilio returns a 201 with a SID and
fails the message afterwards. Nothing raised, so nothing retried.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.messaging import twilio_client

# Captured at import, before conftest's autouse fixture pins the window open:
# these are the tests OF that routing, so they need the real implementation.
_REAL_WITHIN_WINDOW = twilio_client.within_window

PHONE = "whatsapp:+447877178815"
IMAGE = "https://cdn.test/storage/v1/object/public/post-images/abc-123.png"


def _ago(**kw) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


@pytest.fixture(autouse=True)
def _real_window(monkeypatch):
    monkeypatch.setattr(twilio_client, "within_window", _REAL_WITHIN_WINDOW)


@pytest.fixture
def sends(monkeypatch):
    """Capture what would go to Twilio, by route."""
    calls = SimpleNamespace(media=[], template=[])
    monkeypatch.setattr(
        twilio_client, "send_media", lambda to, body, url, **k: _val(calls.media, (to, body, url))
    )
    monkeypatch.setattr(
        twilio_client,
        "send_template",
        lambda to, **k: _val(calls.template, (to, k["content_sid"], k["variables"])),
    )
    monkeypatch.setattr(
        twilio_client, "get_settings", lambda: SimpleNamespace(whatsapp_template_sid="HXtest")
    )
    return calls


async def _val(bucket: list, item) -> str:
    bucket.append(item)
    return "SM123"


def _last_inbound(monkeypatch, value: str | None) -> None:
    async def fake(_phone):
        return value

    monkeypatch.setattr(twilio_client.history, "last_inbound_at", fake)


# --------------------------------------------------------------------------- #
# the window itself
# --------------------------------------------------------------------------- #


async def test_window_is_open_just_inside_24_hours(monkeypatch) -> None:
    _last_inbound(monkeypatch, _ago(hours=23, minutes=50))
    assert await twilio_client.within_window(PHONE) is True


async def test_window_is_shut_just_past_24_hours(monkeypatch) -> None:
    """Mike's last message was 15:58 on the 13th; the 7am draft on the 15th was
    41 hours later and was dropped with 63016."""
    _last_inbound(monkeypatch, _ago(hours=24, minutes=10))
    assert await twilio_client.within_window(PHONE) is False


async def test_never_written_counts_as_shut(monkeypatch) -> None:
    """The expensive assumption is 'open' — that failure is invisible."""
    _last_inbound(monkeypatch, None)
    assert await twilio_client.within_window(PHONE) is False


async def test_an_unparseable_timestamp_counts_as_shut(monkeypatch) -> None:
    _last_inbound(monkeypatch, "not a date")
    assert await twilio_client.within_window(PHONE) is False


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


async def test_open_window_sends_free_form(monkeypatch, sends) -> None:
    _last_inbound(monkeypatch, _ago(hours=1))
    await twilio_client.send_preview(
        PHONE, "caption body", IMAGE, identity="74/156: Live at IPPE", caption="Post copy."
    )
    assert len(sends.media) == 1
    assert not sends.template


async def test_shut_window_sends_the_approved_template(monkeypatch, sends) -> None:
    _last_inbound(monkeypatch, _ago(hours=30))
    await twilio_client.send_preview(
        PHONE, "caption body", IMAGE, identity="74/156: Live at IPPE", caption="Post copy."
    )
    assert not sends.media
    to, content_sid, variables = sends.template[0]
    assert content_sid == "HXtest"
    assert variables["1"] == "74/156: Live at IPPE"
    assert variables["2"] == "Post copy."
    # Only the filename — the template's media URL hard-codes the domain, because
    # WhatsApp forbids a variable anywhere but the path.
    assert variables["3"] == "abc-123.png"


async def test_a_shut_window_with_no_template_configured_raises(monkeypatch, sends) -> None:
    """Refusing loudly beats a send Twilio accepts and silently drops."""
    _last_inbound(monkeypatch, _ago(hours=30))
    monkeypatch.setattr(
        twilio_client, "get_settings", lambda: SimpleNamespace(whatsapp_template_sid=None)
    )
    with pytest.raises(RuntimeError, match="silently dropped"):
        await twilio_client.send_preview(
            PHONE, "body", IMAGE, identity="x", caption="y"
        )
    assert not sends.media and not sends.template


async def test_try_send_preview_reports_failure_instead_of_raising(monkeypatch, sends) -> None:
    _last_inbound(monkeypatch, _ago(hours=30))
    monkeypatch.setattr(
        twilio_client, "get_settings", lambda: SimpleNamespace(whatsapp_template_sid=None)
    )
    assert await twilio_client.try_send_preview(PHONE, "b", IMAGE, identity="x", caption="y") is None


# --------------------------------------------------------------------------- #
# variable hygiene — Meta rejects newlines/tabs inside a template variable
# --------------------------------------------------------------------------- #


def test_flatten_strips_what_meta_forbids() -> None:
    assert twilio_client.flatten_variable("one\ntwo\tthree     four") == "one two three four"


def test_flatten_truncates_rather_than_being_rejected_for_length() -> None:
    out = twilio_client.flatten_variable("x" * 900, limit=50)
    assert len(out) == 50 and out.endswith("…")


async def test_a_multiline_caption_is_flattened_before_it_reaches_whatsapp(
    monkeypatch, sends
) -> None:
    _last_inbound(monkeypatch, _ago(hours=30))
    await twilio_client.send_preview(
        PHONE,
        "body",
        IMAGE,
        identity="74/156",
        caption="Line one.\n\nLine two.\n\n#Globex #Poultry",
    )
    caption_var = sends.template[0][2]["2"]
    assert "\n" not in caption_var
    assert caption_var == "Line one. Line two. #Globex #Poultry"
