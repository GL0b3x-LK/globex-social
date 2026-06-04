"""Conversation-memory tests — offline (history + summarizer faked).

Covers the memory block format and the rolling-summary trigger: it folds older
messages into the summary only once enough have scrolled out of the recent window.
"""

from __future__ import annotations

from app.ai import memory


async def _async(value):
    return value


def _msg(role, body):
    return {"role": role, "body": body, "media_url": None, "kind": "text"}


async def test_build_context_includes_summary_and_recent(monkeypatch) -> None:
    monkeypatch.setattr(
        memory.history,
        "recent",
        lambda phone, limit: _async([_msg("karen", "hi"), _msg("agent", "hello")]),
    )
    ctx = await memory.build_context("p", "Earlier: discussed Gulfood March.")
    assert "Earlier: discussed Gulfood March." in ctx
    assert "Karen: hi" in ctx
    assert "Assistant: hello" in ctx


async def test_build_context_empty_when_no_history(monkeypatch) -> None:
    monkeypatch.setattr(memory.history, "recent", lambda phone, limit: _async([]))
    assert await memory.build_context("p", None) == ""


async def test_summary_updates_when_enough_scrolled_out(monkeypatch) -> None:
    monkeypatch.setattr(
        memory.history, "count", lambda phone: _async(memory.RECENT_WINDOW + memory.SUMMARY_BATCH)
    )
    monkeypatch.setattr(
        memory.history, "page", lambda phone, off, lim: _async([_msg("karen", "old detail")])
    )
    captured: dict = {}

    async def _fake_transition(phone, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(memory.conversation, "transition", _fake_transition)

    async def _fake_summarize(existing, batch):
        return "ROLLED-UP SUMMARY"

    monkeypatch.setattr(memory, "_summarize", _fake_summarize)

    await memory.maybe_update_summary("p", {"context": {}})
    assert captured["context_patch"]["summary"] == "ROLLED-UP SUMMARY"
    assert captured["context_patch"]["summary_count"] == memory.SUMMARY_BATCH


async def test_summary_skipped_when_not_enough_scrolled(monkeypatch) -> None:
    monkeypatch.setattr(memory.history, "count", lambda phone: _async(memory.RECENT_WINDOW + 1))
    called: list = []

    async def _fake_summarize(existing, batch):
        called.append(1)
        return "x"

    monkeypatch.setattr(memory, "_summarize", _fake_summarize)
    await memory.maybe_update_summary("p", {"context": {}})
    assert not called  # below SUMMARY_BATCH → no summarization
