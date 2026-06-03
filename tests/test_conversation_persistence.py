"""Conversation state survives a process 'restart' — it lives in Supabase, not memory.

Integration test; skips if Supabase isn't reachable (same gate as the other DB tests).
"""

from __future__ import annotations

from app.db import conversations as conv_db

PHONE = "whatsapp:+1persistencetest"


def test_pending_draft_survives_reload(supabase_ready) -> None:
    conv_db.delete(PHONE)
    try:
        conv_db.get_or_create(PHONE)
        conv_db.transition(
            PHONE,
            state="awaiting_approval",
            context_patch={"generated": {"caption": "x", "template_variant": "stats"}},
        )
        # A fresh read is what a restarted process would do — state must come back.
        reloaded = conv_db.get(PHONE)
        assert reloaded is not None
        assert reloaded["state"] == "awaiting_approval"
        assert reloaded["context"]["generated"]["template_variant"] == "stats"
    finally:
        conv_db.delete(PHONE)
