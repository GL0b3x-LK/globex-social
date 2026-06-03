"""The conversation state machine.

Maps every (ConversationState, IntentType) pair to exactly one Action. The
workflow layer dispatches Actions to handlers. Keeping routing as pure data makes
it exhaustively testable — the trap in this phase is an undefined (state, intent)
cell, so the table is generated to cover all of them.
"""

from __future__ import annotations

from enum import StrEnum

from app.ai.intent import IntentType
from app.messaging.conversation import ConversationState


class Action(StrEnum):
    GENERATE = "generate"  # start a new draft (supersedes any pending one)
    APPROVE = "approve"  # approve + publish the pending draft
    EDIT = "edit"  # apply an edit to the pending draft
    CANCEL = "cancel"  # cancel the pending draft
    GREET = "greet"  # greeting / help reply
    NUDGE_PENDING = "nudge_pending"  # remind there's a draft waiting on approval
    NOTHING_PENDING = "nothing_pending"  # nothing in progress to act on
    CLARIFY = "clarify"  # ask what they'd like


_I = IntentType

# A draft is in play (AWAITING_APPROVAL / EDITING).
_WITH_DRAFT: dict[IntentType, Action] = {
    _I.new_post_request: Action.GENERATE,
    _I.approval: Action.APPROVE,
    _I.edit_request: Action.EDIT,
    _I.cancellation: Action.CANCEL,
    _I.greeting: Action.NUDGE_PENDING,
    _I.unclear: Action.NUDGE_PENDING,
}

# Nothing pending (IDLE / AWAITING_CLARIFICATION).
_NO_DRAFT: dict[IntentType, Action] = {
    _I.new_post_request: Action.GENERATE,
    _I.approval: Action.NOTHING_PENDING,
    _I.edit_request: Action.NOTHING_PENDING,
    _I.cancellation: Action.NOTHING_PENDING,
    _I.greeting: Action.GREET,
    _I.unclear: Action.CLARIFY,
}

ROUTING: dict[tuple[ConversationState, IntentType], Action] = {}
for _state in (ConversationState.AWAITING_APPROVAL, ConversationState.EDITING):
    for _intent, _action in _WITH_DRAFT.items():
        ROUTING[(_state, _intent)] = _action
for _state in (ConversationState.IDLE, ConversationState.AWAITING_CLARIFICATION):
    for _intent, _action in _NO_DRAFT.items():
        ROUTING[(_state, _intent)] = _action


def route(state: ConversationState, intent: IntentType) -> Action:
    """The single source of truth for 'given where we are and what Karen said, do what?'"""
    return ROUTING[(state, intent)]
