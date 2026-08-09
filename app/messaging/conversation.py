"""Conversation state: the StrEnum + async wrappers over the sync DB helpers.

State is persisted in the `conversations` table so a Railway restart never loses
Karen's in-progress draft.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

from app.db import conversations as conv_db

Row = dict[str, Any]


class ConversationState(StrEnum):
    IDLE = "idle"
    AWAITING_APPROVAL = "awaiting_approval"
    EDITING = "editing"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    INTAKE = "intake"  # guided 4-question flow (about / why / photo / template)
    VIDEO = "video"  # a video is in play: script review, generating, or cut review


def state_of(conversation: Row) -> ConversationState:
    """Read the state off a conversation row, defaulting to IDLE on anything unexpected."""
    try:
        return ConversationState(conversation.get("state") or "idle")
    except ValueError:
        return ConversationState.IDLE


async def get_or_create(phone: str) -> Row:
    return await asyncio.to_thread(conv_db.get_or_create, phone)


async def transition(
    phone: str,
    *,
    state: ConversationState | str | None = None,
    current_post_id: str | None = None,
    context_patch: dict[str, Any] | None = None,
) -> Row:
    state_value = state.value if isinstance(state, ConversationState) else state
    return await asyncio.to_thread(
        conv_db.transition,
        phone,
        state=state_value,
        current_post_id=current_post_id,
        context_patch=context_patch,
    )


async def clear_post(phone: str) -> Row:
    return await asyncio.to_thread(conv_db.clear_post, phone)
