"""Intent classification: Karen's incoming message + conversation state -> Intent."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.ai.client import generate_structured
from app.ai.prompts.intent import INTENT_PROMPT


class IntentType(StrEnum):
    new_post_request = "new_post_request"
    new_video_request = "new_video_request"
    approval = "approval"
    edit_request = "edit_request"
    cancellation = "cancellation"
    question = "question"
    greeting = "greeting"
    unclear = "unclear"


class Intent(BaseModel):
    type: IntentType
    extracted_request: str | None = Field(
        default=None, description="For new_post_request: the core post request. Else null."
    )
    edit_feedback: str | None = Field(
        default=None, description="For edit_request: the requested change. Else null."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Classification confidence in [0,1].")


async def classify_intent(
    message: str, conversation_state: Any, memory: str | None = None
) -> Intent:
    """Classify a message. conversation_state may be a str or StrEnum (e.g. 'awaiting_approval')."""
    state = getattr(conversation_state, "value", conversation_state) or "idle"
    memory_block = f"{memory}\n\n" if memory else ""
    user_content = (
        f"{memory_block}"
        f"Current conversation state: {str(state).upper()}\n\n"
        f"Message from Karen:\n{message}"
    )
    return await generate_structured(
        system=INTENT_PROMPT,
        user_content=user_content,
        output_model=Intent,
        tool_name="classify_intent",
        tool_description="Classify the message into exactly one intent type.",
        max_tokens=512,
    )
