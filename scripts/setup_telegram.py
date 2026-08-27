"""One-time Telegram wiring: point the bot's webhook at this deployment.

Usage:
    python scripts/setup_telegram.py https://<railway-domain>

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from the environment
(.env locally). Registers <base>/webhooks/telegram with the secret token,
then prints getMe + getWebhookInfo so success is verified in the same run —
a setWebhook that "succeeded" against the wrong URL is otherwise invisible
until the first message never arrives.

Run it again any time the domain changes; setWebhook is idempotent.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402


async def main(base_url: str) -> int:
    settings = get_settings()
    token = settings.telegram_bot_token
    secret = settings.telegram_webhook_secret
    if not token or not secret:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must both be set.")
        print(
            'Generate a secret with:  python -c "import secrets; print(secrets.token_urlsafe(24))"'
        )
        return 1

    api = f"https://api.telegram.org/bot{token}"
    webhook_url = f"{base_url.rstrip('/')}/webhooks/telegram"
    async with httpx.AsyncClient(timeout=15.0) as client:
        me = (await client.post(f"{api}/getMe")).json()
        if not me.get("ok"):
            print(f"getMe failed — is the token right? {me}")
            return 1
        bot = me["result"]
        print(f"bot: @{bot.get('username')}  (id {bot.get('id')})")

        set_resp = (
            await client.post(
                f"{api}/setWebhook",
                json={
                    "url": webhook_url,
                    "secret_token": secret,
                    # message = everything operators send; my_chat_member = the
                    # bot being added to the team group (logs the chat id).
                    "allowed_updates": ["message", "my_chat_member"],
                },
            )
        ).json()
        print(f"setWebhook: {set_resp}")

        info = (await client.post(f"{api}/getWebhookInfo")).json().get("result", {})
        print(f"registered url : {info.get('url')}")
        print(f"pending updates: {info.get('pending_update_count')}")
        if err := info.get("last_error_message"):
            print(f"last error     : {err}")
        ok = info.get("url") == webhook_url and set_resp.get("ok")
        print("OK — Telegram will deliver to this deployment." if ok else "MISMATCH — check above.")
        return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
