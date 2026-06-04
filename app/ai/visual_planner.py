"""Visual planner: decide a free-form request's visual treatment before writing copy.

Returns one of three treatments — a designed typographic template (the default),
a generated photographic image (with the brand template overlaid), or a request to
ask Karen which she wants. When generating, it also writes the brand-safe image
prompt. Kept separate from copy generation so the "ask when unsure" decision is
clean — that conversational instinct is the whole point of this step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.client import generate_structured
from app.ai.prompts.brand import BRAND_BLOCK
from app.ai.prompts.visual_plan import VISUAL_PLAN_PROMPT

Treatment = Literal["typographic", "generated_image", "clarify"]


class VisualPlan(BaseModel):
    treatment: Treatment = Field(
        description="typographic = designed template; generated_image = AI photo + overlay; "
        "clarify = ask Karen which she wants."
    )
    image_prompt: str | None = Field(
        default=None,
        description="When generated_image: the brand-safe prompt for the image model "
        "(no text/logos in the image). Null otherwise.",
    )
    clarification: str | None = Field(
        default=None,
        description="When clarify: one short, friendly question to ask Karen. Null otherwise.",
    )
    rationale: str = Field(description="One sentence: why this treatment fits the request.")


def _system() -> str:
    return f"{BRAND_BLOCK}\n\n---\n\n{VISUAL_PLAN_PROMPT}"


async def plan_visual(request: str, memory: str | None = None) -> VisualPlan:
    """Decide how a free-form post should look (and how to ask if unsure)."""
    memory_block = f"{memory}\n\n" if memory else ""
    return await generate_structured(
        system=_system(),
        user_content=f"{memory_block}Karen's request:\n{request}",
        output_model=VisualPlan,
        tool_name="plan_visual",
        tool_description="Decide the post's visual treatment and, if generating, its image prompt.",
        max_tokens=700,
    )
