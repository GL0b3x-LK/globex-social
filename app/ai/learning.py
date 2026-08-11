"""Standing style rules, learned from the operator's corrections.

The edit loop fixes one post; this module decides whether a correction was
really about ONE post or about EVERY post. "Change the date to Friday" dies
with its post. "Stop calling everything premium" is a preference, and a
preference forgotten is a client correcting the same mistake weekly until they
stop trusting the system.

A correction is classified as one of:

* ``one_time``  — tied to this post's specifics; applied and forgotten;
* ``standing``  — a durable preference; saved, confirmed to the operator, and
  injected into every future generation;
* ``unsure``    — the operator is asked ("reply *always* …") and their next
  message settles it. Never blocks: any other reply just carries on.

Rules live as one JSON object in Supabase Storage — shared by local and
Railway, durable across deploys, and needing no schema change. They are
injected BELOW the contractual no-say list and BELOW whatever the operator is
asking for right now: learned preferences are defaults, not law.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.ai.client import generate_structured
from app.db import storage
from app.logging_config import get_logger

log = get_logger("app.ai.learning")

RULES_PATH = "learned/style-rules.json"
MAX_RULES = 30  # a prompt-sized budget; oldest rules yield when it is exceeded


class Rule(BaseModel):
    id: str
    rule: str
    source_feedback: str = ""
    source: str = ""  # who taught it (whatsapp:+...)
    learned_at: str = ""


class Decision(BaseModel):
    scope: Literal["one_time", "standing", "unsure"] = Field(
        description=(
            "'standing' when the correction states a durable preference that should "
            "shape every future post; 'one_time' when it is tied to this post's "
            "specifics; 'unsure' when it genuinely reads both ways."
        )
    )
    rule: str = Field(
        description=(
            "For standing/unsure: the preference restated as one durable imperative "
            "sentence, generalized away from this post (e.g. 'Write on-image titles "
            "in ALL CAPS'). Empty for one_time."
        )
    )
    reason: str = Field(description="One short sentence for the log.")


_CLASSIFY_SYSTEM = """An operator just corrected a draft social post for Globex International (a food trading company). Decide whether the correction is a durable preference or a one-off fix.

standing — states or implies a lasting way of working: "always…", "never…", "stop doing…", "from now on…", "all posts…", or a generic style directive with no tie to this particular post ("no em dashes", "don't call products premium", "keep captions shorter", "titles in caps").
one_time — tied to THIS post's content: its dates, names, numbers, this photo, this specific wording ("change the date to Friday", "say drumsticks here", "use a cold-store photo for this one", "shorten THIS caption").
unsure — genuinely reads both ways ("make the title all caps" — this title, or titles in general?). Prefer unsure over guessing standing: saving a wrong rule pollutes every future post, asking costs one message.

For standing and unsure, restate the preference as ONE durable imperative sentence that stands alone months from now."""


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def load_rules() -> list[Rule]:
    """Every learned rule, oldest first. Missing or unreadable store = no rules."""
    raw = storage.read_bytes(RULES_PATH)
    if not raw:
        return []
    try:
        doc = json.loads(raw.decode("utf-8"))
        return [Rule.model_validate(r) for r in doc.get("rules", [])]
    except Exception as exc:  # noqa: BLE001 — a corrupt store must not break generation
        log.error("could not parse learned rules", extra={"error": str(exc)[:120]})
        return []


def _write(rules: list[Rule]) -> None:
    payload = json.dumps(
        {"rules": [r.model_dump() for r in rules]}, indent=2, ensure_ascii=False
    ).encode("utf-8")
    storage.upload_bytes(RULES_PATH, payload, "application/json")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9 ]+", "", text).strip()


def save_rule(rule_text: str, *, source_feedback: str = "", source: str = "") -> Rule:
    """Persist a standing rule, replacing any near-duplicate rather than stacking.

    "No em dashes" taught twice must stay ONE rule — the store is prompt budget,
    and a duplicate would read as emphasis the operator never intended.
    """
    rules = load_rules()
    incoming = _normalize(rule_text)
    kept: list[Rule] = []
    for r in rules:
        existing = _normalize(r.rule)
        if existing == incoming or existing in incoming or incoming in existing:
            log.info("replacing near-duplicate rule", extra={"old": r.rule[:80]})
            continue
        kept.append(r)
    new = Rule(
        id=f"r-{uuid4().hex[:8]}",
        rule=rule_text.strip(),
        source_feedback=source_feedback[:500],
        source=source,
        learned_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    kept.append(new)
    if len(kept) > MAX_RULES:
        dropped = kept[: len(kept) - MAX_RULES]
        kept = kept[len(kept) - MAX_RULES :]
        log.warning("rule budget exceeded; oldest dropped", extra={"n": len(dropped)})
    _write(kept)
    return new


def remove_rule(index: int | None = None) -> Rule | None:
    """Remove by 1-based list position, or the most recently learned when None."""
    rules = load_rules()
    if not rules:
        return None
    if index is None:
        victim = max(rules, key=lambda r: r.learned_at)
    elif 1 <= index <= len(rules):
        victim = rules[index - 1]
    else:
        return None
    _write([r for r in rules if r.id != victim.id])
    return victim


# --------------------------------------------------------------------------- #
# prompt + WhatsApp surfaces
# --------------------------------------------------------------------------- #


def rules_block() -> str:
    """The learned rules as a prompt section; empty string when nothing is learned."""
    rules = load_rules()
    if not rules:
        return ""
    lines = "\n".join(f"- {r.rule}" for r in rules)
    return (
        "\n\nOPERATOR STYLE RULES — learned from their corrections to earlier posts. "
        "Follow every one of them in this post, unless today's instruction explicitly "
        "asks otherwise. The forbidden-claims rules above still outrank these:\n"
        f"{lines}"
    )


async def rules_block_async() -> str:
    return await asyncio.to_thread(rules_block)


def format_rules_list() -> str:
    """The numbered list an operator sees when they ask what has been learned."""
    rules = load_rules()
    if not rules:
        return "Nothing learned yet — when a correction should apply to every post, I'll remember it here."
    lines = "\n".join(f"{i}. {r.rule}" for i, r in enumerate(rules, start=1))
    return f"📌 What I've learned:\n{lines}\n\nReply *forget rule N* to drop one."


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #


async def consider(feedback: str) -> Decision:
    """Is this correction a one-off, a standing preference, or worth asking about?"""
    return await generate_structured(
        system=_CLASSIFY_SYSTEM,
        user_content=f"The operator's correction:\n{feedback}",
        output_model=Decision,
        tool_name="classify_correction",
        tool_description="Decide whether the correction is one-off, standing, or unsure.",
        max_tokens=400,
    )
