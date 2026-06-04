"""Conversation memory: a recent-message window + a rolling summary of older history.

`build_context` produces the memory block prepended to the AI calls (intent,
planning, generation, Q&A) so the assistant remembers the thread like a colleague.
`maybe_update_summary` folds messages that scroll out of the recent window into a
running summary stored on the conversation row — bounded cost no matter how long
the thread grows.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.ai.client import generate_structured
from app.ai.prompts.memory import SUMMARY_PROMPT
from app.logging_config import get_logger
from app.messaging import conversation, history

log = get_logger("app.ai.memory")
Row = dict[str, Any]

RECENT_WINDOW = 25  # messages always shown verbatim
SUMMARY_BATCH = 12  # fold older messages into the summary once this many have scrolled out

_ROLE_LABEL = {"karen": "Karen", "agent": "Assistant"}


class _Summary(BaseModel):
    summary: str


def _line(msg: Row) -> str:
    who = _ROLE_LABEL.get(msg.get("role", ""), "?")
    body = (msg.get("body") or "").strip()
    if not body and msg.get("media_url"):
        body = f"[{msg.get('kind', 'media')}]"
    return f"{who}: {body}"


def _transcript(msgs: list[Row]) -> str:
    return "\n".join(_line(m) for m in msgs if (m.get("body") or m.get("media_url")))


async def build_context(phone: str, summary: str | None) -> str:
    """The memory block: rolling summary of older history + the recent window."""
    recent = await history.recent(phone, RECENT_WINDOW)
    parts: list[str] = []
    if summary:
        parts.append(f"[Earlier conversation summary]\n{summary}")
    transcript = _transcript(recent)
    if transcript:
        parts.append(f"[Recent messages]\n{transcript}")
    if not parts:
        return ""
    return "## Conversation so far (for context)\n" + "\n\n".join(parts)


async def _summarize(existing: str, batch: list[Row]) -> str:
    user_content = (
        f"EXISTING summary:\n{existing or '(none yet)'}\n\n"
        f"NEW older messages to fold in:\n{_transcript(batch)}"
    )
    result = await generate_structured(
        system=SUMMARY_PROMPT,
        user_content=user_content,
        output_model=_Summary,
        tool_name="emit_summary",
        tool_description="Emit the updated running conversation summary.",
        max_tokens=600,
    )
    return result.summary.strip()


async def maybe_update_summary(phone: str, convo: Row) -> None:
    """Fold messages that have scrolled past the recent window into the summary."""
    total = await history.count(phone)
    older = total - RECENT_WINDOW  # messages no longer in the verbatim window
    context = convo.get("context") or {}
    summarized = int(context.get("summary_count") or 0)
    if older - summarized < SUMMARY_BATCH:
        return  # not enough new history to bother re-summarizing

    batch = await history.page(phone, summarized, older - summarized)
    if not batch:
        return
    new_summary = await _summarize(context.get("summary") or "", batch)
    await conversation.transition(
        phone, context_patch={"summary": new_summary, "summary_count": older}
    )
    log.info("conversation summary updated", extra={"phone": phone, "summarized_through": older})
