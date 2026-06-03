"""Live intent-classification tests (gated by RUN_AI_LIVE). Real Claude calls.

The golden set mixes clear cases (exact expected type) and inherently ambiguous
ones (a small set of acceptable types). The key state-awareness assertion: a bare
"yes" while IDLE must NOT be classified as approval.

Run:  RUN_AI_LIVE=1 .venv\\Scripts\\python.exe -m pytest tests/test_intent.py -v
"""
from __future__ import annotations

import pytest

from app.ai.intent import IntentType, classify_intent

pytestmark = pytest.mark.usefixtures("anthropic_live")

AWAIT = "awaiting_approval"
IDLE = "idle"
T = IntentType

# (message, state, acceptable_types)
GOLDEN: list[tuple[str, str, set[IntentType]]] = [
    # Approvals — only valid with a pending draft
    ("approve", AWAIT, {T.approval}),
    ("yes", AWAIT, {T.approval}),
    ("looks good", AWAIT, {T.approval}),
    ("perfect, send it", AWAIT, {T.approval}),
    ("ship it", AWAIT, {T.approval}),
    ("👍", AWAIT, {T.approval}),
    ("Like 1?", AWAIT, {T.approval, T.unclear}),
    # Edits
    ("make it shorter", AWAIT, {T.edit_request}),
    ("change the headline to '33 years strong'", AWAIT, {T.edit_request}),
    ("drop the emoji", AWAIT, {T.edit_request}),
    ("can you make it more formal", AWAIT, {T.edit_request}),
    ("nope", AWAIT, {T.edit_request, T.cancellation}),
    # Cancellations
    ("cancel", AWAIT, {T.cancellation}),
    ("wait nvm", AWAIT, {T.cancellation}),
    ("forget it", AWAIT, {T.cancellation}),
    # New post requests (any state)
    ("post about us at SIAL Paris", IDLE, {T.new_post_request}),
    ("make something for National Poultry Day", IDLE, {T.new_post_request}),
    ("can you do a post about us hitting 150 ships on the water", IDLE, {T.new_post_request}),
    ("post about our new duck products", AWAIT, {T.new_post_request}),
    # Greetings / small talk
    ("hi", IDLE, {T.greeting}),
    ("good morning", IDLE, {T.greeting}),
    ("thanks!", IDLE, {T.greeting}),
    ("got it", IDLE, {T.greeting}),
    # State-awareness: bare yes/ok while IDLE is NOT approval
    ("yes", IDLE, {T.greeting, T.unclear}),
    ("ok", IDLE, {T.greeting, T.unclear}),
    # Genuinely unclear
    ("hmm", IDLE, {T.unclear, T.greeting}),
    ("???", IDLE, {T.unclear}),
    # More realistic phrasings (corrections, specifics, longer asks)
    ("post about us at the IPPE show next week, we'll have a booth", IDLE, {T.new_post_request}),
    ("no the date is wrong, it's the 19th not the 18th", AWAIT, {T.edit_request}),
    ("can you add our booth number, it's 4521", AWAIT, {T.edit_request}),
    ("yes do it", AWAIT, {T.approval}),
    ("hey can you whip up something about our new pet food line going to 12 new markets", IDLE, {T.new_post_request}),
    ("actually hold off on that one", AWAIT, {T.cancellation}),
]


@pytest.mark.parametrize(
    "message,state,acceptable",
    GOLDEN,
    ids=[f"{i:02d}_{m[:18]}" for i, (m, _, _) in enumerate(GOLDEN)],
)
async def test_intent_golden(message, state, acceptable):
    intent = await classify_intent(message, state)
    assert intent.type in acceptable, (
        f"{message!r} @ {state} -> {intent.type} (conf {intent.confidence}); expected one of {acceptable}"
    )


async def test_new_post_request_extracts_the_request():
    intent = await classify_intent("post about us at SIAL Paris", IDLE)
    assert intent.type is T.new_post_request
    assert intent.extracted_request and "sial" in intent.extracted_request.lower()


async def test_edit_request_captures_feedback():
    intent = await classify_intent("make the headline shorter", AWAIT)
    assert intent.type is T.edit_request
    assert intent.edit_feedback and "short" in intent.edit_feedback.lower()
