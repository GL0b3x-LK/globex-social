"""Anthropic client singleton + structured-output helper.

Structured output uses **forced tool-use**: we declare one "emit" tool whose
input schema is the target Pydantic model, force ``tool_choice`` to it, and
validate the returned tool input with Pydantic. This is robust across models and
does not depend on ``output_config.format`` (not guaranteed on Opus 4.7).

Notes / deviations from the original plan:
- **No ``temperature``** — Opus 4.7 removed ``temperature``/``top_p``/``top_k``
  (sending them is a 400). Brand consistency comes from the prompt + forced
  schema, not a sampling knob.
- Model comes from ``settings.claude_model`` (``claude-opus-4-7``); bumping to
  ``claude-opus-4-8`` is a one-line .env change.
- Retries use the SDK's built-in exponential backoff (``max_retries``); a single
  application-level re-prompt handles schema-validation misses.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.ai.client")

UserContent = str | list[dict[str, Any]]


@lru_cache
def get_client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3)


def _first_tool_input(response: Any) -> dict[str, Any] | None:
    """Return the input dict of the first tool_use block, or None."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            data = getattr(block, "input", None)
            if isinstance(data, dict):
                return data
    return None


async def generate_structured[T: BaseModel](
    *,
    system: str,
    user_content: UserContent,
    output_model: type[T],
    tool_name: str,
    tool_description: str,
    max_tokens: int = 1500,
    max_attempts: int = 2,
) -> T:
    """Force Claude to emit `output_model` via a single tool call; validate it.

    Retries once with a corrective turn if the first emission fails validation.
    """
    client = get_client()
    settings = get_settings()
    tool = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": output_model.model_json_schema(),
    }
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    last_error: str | None = None

    for attempt in range(max_attempts):
        params: dict[str, Any] = {
            "model": settings.claude_model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool_name},
            "messages": messages,
        }
        response = await client.messages.create(**params)
        tool_input = _first_tool_input(response)

        if tool_input is None:
            last_error = "no tool_use block in response"
        else:
            try:
                return output_model.model_validate(tool_input)
            except ValidationError as exc:
                last_error = str(exc)
                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": (
                            f"That failed schema validation:\n{exc}\n"
                            f"Call {tool_name} again with corrected, valid input."
                        ),
                    },
                ]
        log.warning(
            "structured generation retry",
            extra={"tool": tool_name, "attempt": attempt, "error": (last_error or "")[:300]},
        )

    raise RuntimeError(
        f"structured generation for {tool_name} failed after {max_attempts} attempts: {last_error}"
    )
