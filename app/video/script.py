"""The screenplay engine: brief -> VideoScript, plus the checks that keep it honest.

Two validators run after every generation and every revision, because a prompt
is a request and a check is a guarantee:

* **claims lint** — the client's no-say list ('Halal', '90+ countries',
  'inspected by hand') is scanned for in code, not hoped away by instructions;
* **timing physics** — spoken words are measured against the scene length at a
  real speaking rate, so an approved 30-second script actually runs 30 seconds
  instead of 48.

A script that fails either is sent back to the model with the specific failure
named, which is far more reliable than asking it to be careful up front.
"""

from __future__ import annotations

from app.ai.client import generate_structured
from app.logging_config import get_logger
from app.video import library
from app.video.models import Brief, Scene, VideoMode, VideoScript

log = get_logger("app.video.script")

# Measured from the approved UGC video's voiceover: ~2.5 words/second is a
# professional read. Scenes are checked against this, with headroom for breath.
WORDS_PER_SECOND = 2.5
_TIMING_TOLERANCE = 1.15  # allow 15% over before rejecting

MIN_SCENE_SECONDS = 3.0
MAX_SCENE_SECONDS = 12.0
MAX_TOTAL_SECONDS = 90.0  # Facebook only accepts API video as Reels: 3-90s

_MAX_REPAIRS = 2

BRIEF_TOOL_DESC = "Parse the operator's video request into its parts."
SCRIPT_TOOL_DESC = "Emit the finished video screenplay."

_STYLE_RULES = """
HOUSE STYLE — these come from the client's own written feedback. Breaking any of
them means the video is rejected:
- Real, grounded, documentary feel. Handheld. Hard cuts. Never "PowerPointy".
- Professional but human. Direct and confident. No hype, no salesmanship.
- NEVER mention: Halal, "90+ countries", "inspected by hand".
  Say "shipped globally" and "Quality Control" instead.
- No trailing periods in the caption.
- NEVER invent a fact: no city, country, port, customer, date, percentage or
  volume unless it was given to you above. Write "City, Country" in full only
  when you were actually told the place. When in doubt, say less.
- No steam, smoke, particles or spotlights in any scene description.
- Never describe raw meat, carcasses or blood. Product appears packaged.
- Never describe the character's face, clothing colour, or the packaging design
  in keyframe_prompt — those come from reference photographs. Describe only the
  setting, the framing and what is happening.
- Every scene's dialogue must be sayable in its `seconds` at 2.5 words/second.
"""


def _brief_system() -> str:
    names = ", ".join(c.name for c in library.load_characters() if c.usable)
    return (
        "You parse short WhatsApp requests for company videos into structured parts.\n"
        f"Known characters: {names}.\n"
        "If the operator names someone not on that list, still return the name they used.\n"
        "If no character is named, return null — a voiceover video is valid.\n"
        "target_seconds defaults to 30 when unstated."
    )


async def parse_brief(message: str) -> Brief:
    return await generate_structured(
        system=_brief_system(),
        user_content=message,
        output_model=Brief,
        tool_name="emit_brief",
        tool_description=BRIEF_TOOL_DESC,
        max_tokens=600,
    )


def _script_system(
    character: library.Character | None, product: library.Product | None, target_seconds: int
) -> str:
    parts = [
        "You are the creative director for Globex International, a global food trading "
        "company founded in 1993 that ships poultry and other proteins worldwide. You "
        "direct short vertical social videos.\n\n"
        "Work like a director, not a copywriter. Decide what the viewer sees first, what "
        "holds their attention, and what they are left with. Every scene needs a reason "
        "to exist and a reason to cut. Then write the direction precisely enough that a "
        "camera operator and a video model could both execute it without asking a "
        "question — concrete, physical, specific about movement and timing.\n\n"
        "The reference this must match is real factory footage: handheld, documentary, "
        "unglamorous, shot on a working floor. Nothing staged, nothing corporate.",
        _STYLE_RULES,
        f"TARGET LENGTH: about {target_seconds} seconds across 3-5 scenes. "
        f"Each scene {MIN_SCENE_SECONDS:.0f}-{MAX_SCENE_SECONDS:.0f} seconds.",
    ]
    if character:
        parts.append(
            f"PRESENTER: {character.name}, {character.role}. {character.persona}\n"
            f"How they speak: {character.speaking_style}\n"
            f"Write their lines in their voice. Scenes where they speak on camera are "
            f"kind='speaking'; cutaways are kind='broll' and their words continue as "
            f"voiceover. At least one scene must be 'speaking'."
        )
    else:
        parts.append(
            "No presenter: this is a voiceover video. EVERY scene is kind='broll' and "
            "the dialogue is narration over the footage."
        )
    if product:
        points = "\n".join(f"- {p}" for p in product.talking_points)
        parts.append(
            f"PRODUCT: {product.name}. {product.description}\n"
            f"True things you may say:\n{points}\n"
            "The product appears in its real packaging, which comes from a photograph. "
            "Do not invent packaging, labels or text."
        )
        if product.claims_forbidden:
            parts.append("NEVER say: " + ", ".join(product.claims_forbidden))
    return "\n\n".join(parts)


def lint(script: VideoScript, product: library.Product | None = None) -> list[str]:
    """Every rule violation in a script. Empty means it may be shown to the client."""
    problems: list[str] = []

    banned = library.banned_terms_in(script.spoken_text + " " + script.caption, product)
    for term in banned:
        problems.append(
            f"The phrase '{term}' is on the client's forbidden list — rewrite without it."
        )

    if script.caption.rstrip().endswith("."):
        problems.append("The caption ends with a period; the client rejects trailing periods.")

    if not script.scenes:
        problems.append("The script has no scenes.")
    if len(script.scenes) > 6:
        problems.append(f"{len(script.scenes)} scenes is too many; use 3-5.")

    if script.mode is VideoMode.presenter and not any(
        s.kind.value == "speaking" for s in script.scenes
    ):
        problems.append("Presenter mode needs at least one 'speaking' scene.")
    if script.mode is VideoMode.voiceover and any(
        s.kind.value == "speaking" for s in script.scenes
    ):
        problems.append("Voiceover mode cannot contain a 'speaking' scene.")

    for scene in script.scenes:
        where = f"Scene {scene.idx}"
        if not scene.dialogue.strip():
            problems.append(f"{where} has no dialogue.")
        if not scene.motion_prompt.strip():
            problems.append(f"{where} has no shot direction for the video model.")
        if not scene.beats:
            problems.append(f"{where} has no beats.")
        if not MIN_SCENE_SECONDS <= scene.seconds <= MAX_SCENE_SECONDS:
            problems.append(
                f"{where} is {scene.seconds:.0f}s; scenes must be "
                f"{MIN_SCENE_SECONDS:.0f}-{MAX_SCENE_SECONDS:.0f}s."
            )
        problems.extend(_timing_problem(scene) or [])
        for word in ("steam", "smoke", "spotlight", "particles", "carcass", "blood"):
            haystack = " ".join(
                [scene.keyframe_prompt, scene.action, scene.motion_prompt, *scene.beats]
            ).lower()
            if word in haystack:
                problems.append(f"{where} describes '{word}', which the client rejected.")

    if script.total_seconds > MAX_TOTAL_SECONDS:
        problems.append(
            f"Total {script.total_seconds:.0f}s exceeds the {MAX_TOTAL_SECONDS:.0f}s "
            "platform limit for Reels."
        )
    return problems


def _timing_problem(scene: Scene) -> list[str]:
    words = len(scene.dialogue.split())
    speakable = scene.seconds * WORDS_PER_SECOND * _TIMING_TOLERANCE
    if words > speakable:
        budget = int(scene.seconds * WORDS_PER_SECOND)
        return [
            f"Scene {scene.idx} has {words} words but only {scene.seconds:.0f}s "
            f"— cut it to about {budget} words."
        ]
    return []


def renumber(script: VideoScript) -> VideoScript:
    """Force scene indices to be 1..N in order, whatever the model emitted."""
    for i, scene in enumerate(script.scenes, start=1):
        scene.idx = i
    return script


async def write_script(
    brief: Brief,
    character: library.Character | None,
    product: library.Product | None,
) -> tuple[VideoScript, list[str]]:
    """Generate a script and repair it until it passes the checks.

    Returns the script plus any problems that survived the repair attempts, so a
    caller can decide whether to surface them rather than failing silently.
    """
    system = _script_system(character, product, brief.target_seconds)
    ask = f"Video brief: {brief.angle}\nRequested length: about {brief.target_seconds} seconds."
    if brief.must_say:
        ask += "\nThe operator asked you to include: " + "; ".join(brief.must_say)

    script = renumber(
        await generate_structured(
            system=system,
            user_content=ask,
            output_model=VideoScript,
            tool_name="emit_script",
            tool_description=SCRIPT_TOOL_DESC,
            max_tokens=6000,
        )
    )

    for attempt in range(_MAX_REPAIRS):
        problems = lint(script, product)
        if not problems:
            return script, []
        log.info("script repair", extra={"attempt": attempt + 1, "problems": len(problems)})
        script = renumber(
            await generate_structured(
                system=system,
                user_content=(
                    f"{ask}\n\nYour previous script had these problems:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + "\n\nWrite the whole script again, fixing every one of them."
                ),
                output_model=VideoScript,
                tool_name="emit_script",
                tool_description=SCRIPT_TOOL_DESC,
                max_tokens=6000,
            )
        )
    return script, lint(script, product)


async def revise(
    script: VideoScript,
    feedback: str,
    character: library.Character | None,
    product: library.Product | None,
    target_seconds: int,
) -> tuple[VideoScript, list[str]]:
    """Apply plain-English feedback, returning a complete new script."""
    system = _script_system(character, product, target_seconds)
    ask = (
        "Here is the current script as JSON:\n"
        f"{script.model_dump_json(indent=2)}\n\n"
        f"The operator asked for this change:\n{feedback}\n\n"
        "Return the COMPLETE revised script. Change only what the feedback asks for; "
        "leave everything else exactly as it is."
    )
    revised = renumber(
        await generate_structured(
            system=system,
            user_content=ask,
            output_model=VideoScript,
            tool_name="emit_script",
            tool_description=SCRIPT_TOOL_DESC,
            max_tokens=6000,
        )
    )
    problems = lint(revised, product)
    if problems:
        revised = renumber(
            await generate_structured(
                system=system,
                user_content=(
                    ask
                    + "\n\nYour revision had these problems:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + "\n\nFix every one of them."
                ),
                output_model=VideoScript,
                tool_name="emit_script",
                tool_description=SCRIPT_TOOL_DESC,
                max_tokens=6000,
            )
        )
        problems = lint(revised, product)
    return revised, problems


def diff_summary(old: VideoScript, new: VideoScript) -> str:
    """A human line describing what a revision actually changed."""
    changes: list[str] = []
    if old.title != new.title:
        changes.append("title")
    if len(old.scenes) != len(new.scenes):
        changes.append(f"scene count ({len(old.scenes)} → {len(new.scenes)})")
    else:
        moved = [
            str(a.idx)
            for a, b in zip(old.scenes, new.scenes, strict=True)
            if a.dialogue != b.dialogue or a.action != b.action or a.seconds != b.seconds
        ]
        if moved:
            changes.append(f"scene {', '.join(moved)}")
    if old.music_mood != new.music_mood:
        changes.append("music")
    if old.caption != new.caption:
        changes.append("caption")
    return ", ".join(changes) if changes else "nothing substantive"
