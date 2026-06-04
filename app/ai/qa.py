"""Conversational Q&A: answer Karen's questions from memory + the posts history.

Used when intent is `question` (e.g. "how many posts this month?", "what did we
run for Ramadan?", "show me the Gulfood one", or a swipe-reply asking about a
specific past post). The answer can reference a specific post so the caller
re-sends its image.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.ai.client import generate_structured
from app.ai.prompts.qa import QA_PROMPT

Row = dict[str, Any]


class Answer(BaseModel):
    answer: str = Field(description="The conversational reply to send Karen.")
    referenced_post_id: str | None = Field(
        default=None,
        description="If the question is about one specific post in the digest worth showing, "
        "its id. Null otherwise.",
    )


def _caption_snippet(post: Row, n: int = 90) -> str:
    text = (post.get("caption") or post.get("content") or "").strip().replace("\n", " ")
    return text[:n]


def posts_digest(posts: list[Row]) -> str:
    """Compact, model-readable list of recent posts for the Q&A prompt."""
    if not posts:
        return "(no posts yet)"
    lines = []
    for p in posts:
        date = (p.get("created_at") or "")[:10]
        lines.append(
            f"- [{p.get('id')}] {date} · {p.get('status', '?')} · "
            f"{p.get('template_type', '?')} · {_caption_snippet(p)}"
        )
    return "\n".join(lines)


async def answer_question(
    question: str,
    *,
    memory: str,
    digest: str,
    focus: Row | None = None,
) -> Answer:
    parts = [memory] if memory else []
    parts.append(f"[Recent posts]\n{digest}")
    if focus:
        parts.append(
            f"[Karen is asking about this specific post]\n"
            f"id={focus.get('id')} · status={focus.get('status')} · "
            f"template={focus.get('template_type')}\ncaption: {focus.get('caption')}"
        )
    parts.append(f"[Karen's question]\n{question}")
    return await generate_structured(
        system=QA_PROMPT,
        user_content="\n\n".join(parts),
        output_model=Answer,
        tool_name="answer",
        tool_description="Answer Karen's question; optionally reference a specific post to show.",
        max_tokens=600,
    )
