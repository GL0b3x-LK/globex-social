"""The learning layer: corrections that outlive their post.

The contract under test: a standing preference is stored once (not stacked),
injected into every future generation below the contractual rules, removable by
the operator, and the ask-first path can never trap the conversation or steal
an approval.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai import learning
from app.workflows import approval, on_demand


@pytest.fixture()
def store(monkeypatch) -> dict[str, bytes]:
    """An in-memory stand-in for the Supabase Storage object."""
    blobs: dict[str, bytes] = {}

    def fake_read(path: str, **_kw) -> bytes | None:
        return blobs.get(path)

    def fake_upload(path: str, data: bytes, content_type: str, **_kw) -> str:
        blobs[path] = data
        return f"https://cdn.test/{path}"

    monkeypatch.setattr(learning.storage, "read_bytes", fake_read)
    monkeypatch.setattr(learning.storage, "upload_bytes", fake_upload)
    return blobs


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def test_a_rule_taught_twice_is_stored_once(store) -> None:
    learning.save_rule("Never use em dashes in captions")
    learning.save_rule("never use em dashes in captions!")
    rules = learning.load_rules()
    assert len(rules) == 1
    assert rules[0].rule == "never use em dashes in captions!"


def test_the_rule_budget_drops_the_oldest(store) -> None:
    for i in range(learning.MAX_RULES + 3):
        learning.save_rule(f"distinct rule number {i} about topic {i}")
    rules = learning.load_rules()
    assert len(rules) == learning.MAX_RULES
    assert rules[0].rule.startswith("distinct rule number 3")


def test_forget_by_number_and_forget_latest(store) -> None:
    learning.save_rule("first rule about titles")
    learning.save_rule("second rule about captions")
    removed = learning.remove_rule(1)
    assert removed is not None and "first" in removed.rule
    assert len(learning.load_rules()) == 1
    removed = learning.remove_rule(None)
    assert removed is not None and "second" in removed.rule
    assert learning.load_rules() == []


def test_a_corrupt_store_means_no_rules_not_a_crash(store) -> None:
    store[learning.RULES_PATH] = b"{not json"
    assert learning.load_rules() == []
    assert learning.rules_block() == ""


# --------------------------------------------------------------------------- #
# prompt injection
# --------------------------------------------------------------------------- #


def test_rules_reach_the_prompt_below_the_contractual_rules(store) -> None:
    learning.save_rule("Write on-image titles in ALL CAPS")
    block = learning.rules_block()
    assert "ALL CAPS" in block
    assert "forbidden-claims rules above still outrank" in block


@pytest.mark.asyncio
async def test_generation_carries_the_learned_rules(store, monkeypatch) -> None:
    from app.ai import generator
    from app.ai.generator import ContentCategory, GeneratedPost

    learning.save_rule("Never call products premium")
    captured: dict[str, Any] = {}

    async def fake_generate(**kw):
        captured["system"] = kw["system"]
        return GeneratedPost(
            caption="c",
            hashtags=[],
            template_variant="promotional",
            headline="h",
            rationale="r",
        )

    monkeypatch.setattr(generator, "generate_structured", fake_generate)
    await generator.generate_post(ContentCategory.promotional, None, "a post about duck")
    assert "Never call products premium" in captured["system"]


# --------------------------------------------------------------------------- #
# the decision loop after an edit
# --------------------------------------------------------------------------- #


class _Msgs:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.context_patches: list[dict[str, Any]] = []


@pytest.fixture()
def learn_wired(store, monkeypatch) -> _Msgs:
    cap = _Msgs()

    async def fake_send_text(phone, body, **kw):
        cap.texts.append(body)
        return "SM"

    async def fake_transition(phone, **kw):
        if kw.get("context_patch"):
            cap.context_patches.append(kw["context_patch"])
        return {}

    monkeypatch.setattr(approval.twilio_client, "send_text", fake_send_text)
    monkeypatch.setattr(approval.conversation, "transition", fake_transition)
    return cap


@pytest.mark.asyncio
async def test_a_standing_correction_is_saved_and_announced(learn_wired, monkeypatch) -> None:
    async def fake_consider(feedback):
        return learning.Decision(
            scope="standing", rule="Never use exclamation marks", reason="generic style"
        )

    monkeypatch.setattr(approval.learning, "consider", fake_consider)
    await approval._maybe_learn("whatsapp:+1", "stop using exclamation marks everywhere")
    assert [r.rule for r in learning.load_rules()] == ["Never use exclamation marks"]
    assert learn_wired.texts and "📌" in learn_wired.texts[0]


@pytest.mark.asyncio
async def test_an_ambiguous_correction_asks_instead_of_guessing(learn_wired, monkeypatch) -> None:
    async def fake_consider(feedback):
        return learning.Decision(
            scope="unsure", rule="Write titles in ALL CAPS", reason="could be one-off"
        )

    monkeypatch.setattr(approval.learning, "consider", fake_consider)
    await approval._maybe_learn("whatsapp:+1", "make the title all caps")
    assert learning.load_rules() == []  # nothing saved without an answer
    assert learn_wired.context_patches == [{"pending_rule": "Write titles in ALL CAPS"}]
    assert learn_wired.texts and "always" in learn_wired.texts[0].lower()


@pytest.mark.asyncio
async def test_a_one_time_correction_teaches_nothing(learn_wired, monkeypatch) -> None:
    async def fake_consider(feedback):
        return learning.Decision(scope="one_time", rule="", reason="post-specific")

    monkeypatch.setattr(approval.learning, "consider", fake_consider)
    await approval._maybe_learn("whatsapp:+1", "change the date to Friday")
    assert learning.load_rules() == []
    assert learn_wired.texts == []


@pytest.mark.asyncio
async def test_a_broken_classifier_never_breaks_the_edit(learn_wired, monkeypatch) -> None:
    async def exploding_consider(feedback):
        raise RuntimeError("api down")

    monkeypatch.setattr(approval.learning, "consider", exploding_consider)
    await approval._maybe_learn("whatsapp:+1", "anything")  # must not raise


# --------------------------------------------------------------------------- #
# answering the question, and the operator's commands
# --------------------------------------------------------------------------- #


@pytest.fixture()
def od_wired(store, monkeypatch) -> _Msgs:
    cap = _Msgs()

    async def fake_send_text(phone, body, **kw):
        cap.texts.append(body)
        return "SM"

    async def fake_transition(phone, **kw):
        if kw.get("context_patch"):
            cap.context_patches.append(kw["context_patch"])
        return {}

    monkeypatch.setattr(on_demand.twilio_client, "send_text", fake_send_text)
    monkeypatch.setattr(on_demand.conversation, "transition", fake_transition)
    return cap


_PENDING = {"context": {"pending_rule": "Write titles in ALL CAPS"}}


@pytest.mark.asyncio
async def test_always_saves_the_pending_rule(od_wired) -> None:
    assert await on_demand._handle_rule_answer("whatsapp:+1", _PENDING, "always") is True
    assert [r.rule for r in learning.load_rules()] == ["Write titles in ALL CAPS"]


@pytest.mark.asyncio
async def test_just_this_once_declines_it(od_wired) -> None:
    assert await on_demand._handle_rule_answer("whatsapp:+1", _PENDING, "just this once") is True
    assert learning.load_rules() == []


@pytest.mark.asyncio
async def test_an_unrelated_message_clears_the_question_and_flows_on(od_wired) -> None:
    handled = await on_demand._handle_rule_answer(
        "whatsapp:+1", _PENDING, "make a post about our corn program"
    )
    assert handled is False  # the message continues into normal routing
    assert od_wired.context_patches == [{"pending_rule": None}]
    assert learning.load_rules() == []


@pytest.mark.asyncio
async def test_approve_is_never_stolen_as_a_rule_answer(od_wired) -> None:
    """'approve' right after the question approves the POST. The word 'always'
    inside it must not save a rule either ('approve — always loved this one')."""
    assert await on_demand._handle_rule_answer("whatsapp:+1", _PENDING, "approve") is False
    assert learning.load_rules() == []


@pytest.mark.asyncio
async def test_no_message_containing_not_is_mistaken_for_a_decline(od_wired) -> None:
    handled = await on_demand._handle_rule_answer(
        "whatsapp:+1", _PENDING, "not bad but shorten the caption"
    )
    assert handled is False


@pytest.mark.asyncio
async def test_rules_command_lists_what_was_learned(od_wired) -> None:
    learning.save_rule("Never use em dashes")
    assert await on_demand._handle_rule_commands("whatsapp:+1", "rules") is True
    assert "Never use em dashes" in od_wired.texts[-1]


@pytest.mark.asyncio
async def test_forget_rule_n_removes_it(od_wired) -> None:
    learning.save_rule("rule one about titles")
    learning.save_rule("rule two about captions")
    assert await on_demand._handle_rule_commands("whatsapp:+1", "forget rule 1") is True
    assert [r.rule for r in learning.load_rules()] == ["rule two about captions"]


@pytest.mark.asyncio
async def test_ordinary_messages_are_not_commands(od_wired) -> None:
    assert await on_demand._handle_rule_commands("whatsapp:+1", "new post about duck") is False
    assert od_wired.texts == []


def test_saved_rules_round_trip_through_json(store) -> None:
    learning.save_rule("Keep captions under three lines", source="whatsapp:+1")
    doc = json.loads(store[learning.RULES_PATH])
    assert doc["rules"][0]["rule"] == "Keep captions under three lines"
    assert doc["rules"][0]["source"] == "whatsapp:+1"
