"""The Telegram transport: dispatch, sending, and webhook normalisation.

The contract under test is transport-blindness — a workflow that says
"send this to <address>" must behave identically whether the address is
whatsapp:+… or telegram:…, and an inbound Telegram Update must reach
handle_incoming_message in exactly the shape the Twilio webhook produces.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.messaging import messenger, telegram_client, telegram_webhook, twilio_client

SECRET = "test-webhook-secret"
CHAT = "telegram:847213905"
GROUP = "telegram:-1004455"


@pytest.fixture
def telegram_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:testtoken")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("AUTHORIZED_NUMBERS", f"whatsapp:+19178592787,{CHAT},{GROUP}")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api_calls(monkeypatch):
    """Capture Bot API calls; the conftest fake is replaced with a recorder."""
    calls: list[tuple[str, dict]] = []

    async def _record(method, payload, *, timeout=15.0):
        calls.append((method, payload))
        return {"message_id": 42}

    monkeypatch.setattr(telegram_client, "_api", _record)
    return calls


# --- messenger dispatch -----------------------------------------------------


async def test_a_telegram_address_routes_to_the_bot_api(telegram_settings, api_calls):
    await messenger.send_text(CHAT, "hello")
    assert api_calls == [("sendMessage", {"chat_id": "847213905", "text": "hello"})]


async def test_a_whatsapp_address_still_routes_to_twilio(telegram_settings, api_calls, monkeypatch):
    sent = []

    async def fake_send(to, body, *, post_id=None):
        sent.append(to)
        return "SMx"

    monkeypatch.setattr(twilio_client, "send_text", fake_send)
    await messenger.send_text("whatsapp:+19178592787", "hello")
    assert sent == ["whatsapp:+19178592787"] and not api_calls


async def test_a_preview_to_telegram_never_needs_the_window_or_a_template(
    telegram_settings, api_calls
):
    # No within_window lookup, no template SID required — the send just goes.
    sid = await messenger.send_preview(
        CHAT, "caption text", "https://x/img.png", identity="1/156: Test", caption="caption text"
    )
    assert sid == "tg:847213905:42"
    assert api_calls[0][0] == "sendPhoto"


# --- sending ----------------------------------------------------------------


async def test_a_caption_over_telegrams_cap_arrives_whole_as_two_messages(
    telegram_settings, api_calls
):
    long_body = "x" * 2000  # over the 1024 caption cap: must not be truncated
    await telegram_client.send_media(CHAT, long_body, "https://x/img.png")
    methods = [m for m, _ in api_calls]
    assert methods == ["sendPhoto", "sendMessage"]
    assert "caption" not in api_calls[0][1]
    assert api_calls[1][1]["text"] == long_body


async def test_a_group_chat_id_is_carried_negative_and_intact(telegram_settings, api_calls):
    await telegram_client.send_text(GROUP, "hi team")
    assert api_calls[0][1]["chat_id"] == "-1004455"


# --- webhook ----------------------------------------------------------------


@pytest.fixture
def client(telegram_settings):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(telegram_webhook.router)
    return TestClient(app)


def _update(chat_id: int, text: str = "hello", **message_extra):
    message = {"message_id": 7, "chat": {"id": chat_id, "type": "private"}, "text": text}
    message.update(message_extra)
    return {"update_id": 1, "message": message}


def test_a_wrong_secret_token_is_rejected(client):
    resp = client.post(
        "/webhooks/telegram",
        json=_update(847213905),
        headers={"X-Telegram-Bot-Api-Secret-Token": "forged"},
    )
    assert resp.status_code == 403


def test_an_inbound_message_is_normalised_into_the_shared_pipeline(client, monkeypatch):
    received = {}

    async def fake_handle(from_phone, body, media, *, message_sid=None, reply_to_sid=None):
        received.update(
            {
                "from": from_phone,
                "body": body,
                "media": media,
                "sid": message_sid,
                "reply": reply_to_sid,
            }
        )

    monkeypatch.setattr(telegram_webhook.on_demand, "handle_incoming_message", fake_handle)
    update = _update(
        847213905,
        text="approve",
        reply_to_message={"message_id": 3},
    )
    resp = client.post(
        "/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
    )
    assert resp.status_code == 200
    assert received["from"] == CHAT
    assert received["body"] == "approve"
    assert received["sid"] == "tg:847213905:7"
    # The swipe-reply key matches what the outbound side records, so the
    # existing by_sid lookup re-opens the right post.
    assert received["reply"] == "tg:847213905:3"


def test_a_photo_arrives_as_the_largest_size_with_a_pseudo_url(client, monkeypatch):
    received = {}

    async def fake_handle(from_phone, body, media, **kw):
        received["media"] = media
        received["body"] = body

    monkeypatch.setattr(telegram_webhook.on_demand, "handle_incoming_message", fake_handle)
    update = {
        "update_id": 1,
        "message": {
            "message_id": 8,
            "chat": {"id": 847213905, "type": "private"},
            "caption": "use this photo",
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
        },
    }
    client.post(
        "/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
    )
    assert received["media"] == [("telegram-file:large", "image/jpeg")]
    assert received["body"] == "use this photo"


def test_an_unknown_chat_gets_its_id_back_instead_of_silence(client, api_calls, monkeypatch):
    async def boom(*a, **kw):  # the pipeline must never see the message
        raise AssertionError("unauthorized message reached the pipeline")

    monkeypatch.setattr(telegram_webhook.on_demand, "handle_incoming_message", boom)
    resp = client.post(
        "/webhooks/telegram",
        json=_update(555000111),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 200
    method, payload = api_calls[0]
    assert method == "sendMessage"
    assert "telegram:555000111" in payload["text"]


def test_slash_start_reads_as_a_greeting_not_a_riddle(client, monkeypatch):
    received = {}

    async def fake_handle(from_phone, body, media, **kw):
        received["body"] = body

    monkeypatch.setattr(telegram_webhook.on_demand, "handle_incoming_message", fake_handle)
    client.post(
        "/webhooks/telegram",
        json=_update(847213905, text="/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert received["body"] == "hello"


def test_the_bot_being_added_to_a_new_group_announces_the_chat_id(client, api_calls):
    update = {
        "update_id": 2,
        "my_chat_member": {
            "chat": {"id": -100999, "type": "group", "title": "Globex Posts"},
            "new_chat_member": {"status": "member"},
        },
    }
    resp = client.post(
        "/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
    )
    assert resp.status_code == 200
    assert "telegram:-100999" in api_calls[0][1]["text"]
