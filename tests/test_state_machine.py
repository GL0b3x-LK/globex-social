"""Exhaustive conversation state-machine tests (always-on, pure — no I/O).

The trap in this phase is an undefined (state, intent) cell, so the first test
asserts the routing table covers the full cross-product.
"""

from __future__ import annotations

import pytest

from app.ai.intent import IntentType
from app.messaging.conversation import ConversationState
from app.messaging.state_machine import ROUTING, Action, route

ALL_STATES = list(ConversationState)
ALL_INTENTS = list(IntentType)


def test_routing_covers_every_state_intent_pair() -> None:
    for state in ALL_STATES:
        for intent in ALL_INTENTS:
            assert isinstance(route(state, intent), Action), f"undefined: {state} x {intent}"
    assert len(ROUTING) == len(ALL_STATES) * len(ALL_INTENTS) == 28


@pytest.mark.parametrize("state", ALL_STATES)
def test_new_post_request_always_generates(state: ConversationState) -> None:
    assert route(state, IntentType.new_post_request) is Action.GENERATE


def test_approval_only_acts_with_a_pending_draft() -> None:
    assert route(ConversationState.AWAITING_APPROVAL, IntentType.approval) is Action.APPROVE
    assert route(ConversationState.EDITING, IntentType.approval) is Action.APPROVE
    assert route(ConversationState.IDLE, IntentType.approval) is Action.NOTHING_PENDING
    assert (
        route(ConversationState.AWAITING_CLARIFICATION, IntentType.approval)
        is Action.NOTHING_PENDING
    )


def test_edit_only_acts_with_a_pending_draft() -> None:
    assert route(ConversationState.AWAITING_APPROVAL, IntentType.edit_request) is Action.EDIT
    assert route(ConversationState.EDITING, IntentType.edit_request) is Action.EDIT
    assert route(ConversationState.IDLE, IntentType.edit_request) is Action.NOTHING_PENDING


def test_cancellation_with_and_without_a_draft() -> None:
    assert route(ConversationState.AWAITING_APPROVAL, IntentType.cancellation) is Action.CANCEL
    assert route(ConversationState.IDLE, IntentType.cancellation) is Action.NOTHING_PENDING


def test_greeting_depends_on_pending_draft() -> None:
    assert route(ConversationState.IDLE, IntentType.greeting) is Action.GREET
    assert route(ConversationState.AWAITING_APPROVAL, IntentType.greeting) is Action.NUDGE_PENDING


def test_unclear_clarifies_when_idle_but_nudges_when_pending() -> None:
    assert route(ConversationState.IDLE, IntentType.unclear) is Action.CLARIFY
    assert route(ConversationState.AWAITING_APPROVAL, IntentType.unclear) is Action.NUDGE_PENDING


@pytest.mark.parametrize("state", ALL_STATES)
def test_question_is_always_answered(state: ConversationState) -> None:
    assert route(state, IntentType.question) is Action.ANSWER
