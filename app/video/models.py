"""Video engine schemas: the screenplay and the edit spec.

Two documents drive the whole pipeline and both are plain data:

* ``VideoScript`` — what Opus writes and the operator approves. Every scene is
  either someone speaking to camera or b-roll under voiceover, and each carries
  the exact words, the delivery notes, and the instruction for its start frame.
* ``EditSpec`` — how the finished clips are cut together. This is the ONLY thing
  a "make it shorter / different music / drop scene 2" edit touches, which is
  why those edits cost nothing and re-render in about a minute.

Keeping them separate is the whole cost model: generation is expensive and
non-deterministic, assembly is free and deterministic, so as much as possible
lives in the second.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SceneKind(StrEnum):
    speaking = "speaking"  # a character on camera, lip-synced to their voice
    broll = "broll"  # product / plant / port footage under voiceover


class VideoMode(StrEnum):
    presenter = "presenter"  # at least one speaking scene
    voiceover = "voiceover"  # narrator over b-roll — the approved UGC format


class Scene(BaseModel):
    idx: int = Field(description="1-based position in the video.")
    kind: SceneKind = Field(description="'speaking' if the character is on camera saying the line.")
    seconds: float = Field(description="Planned length, 3-12 seconds.")
    setting: str = Field(description="Where this happens, e.g. 'factory floor, packing line'.")
    camera: str = Field(
        description="Camera feel: handheld, slow push-in, static. No fancy transitions."
    )
    action: str = Field(description="What physically happens in shot.")
    dialogue: str = Field(description="The exact words spoken. Never empty.")
    delivery: str = Field(description="How it is said: pace, warmth, emphasis.")
    keyframe_prompt: str = Field(
        description=(
            "How the opening frame looks. Describe the scene ONLY — the character's "
            "appearance and the product packaging come from reference photos, so never "
            "describe their face, clothing colour or packaging design here."
        )
    )


class VideoScript(BaseModel):
    title: str = Field(description="Short internal title, e.g. 'Duck shipping, told by John'.")
    mode: VideoMode
    scenes: list[Scene] = Field(description="3-5 scenes that tell one coherent story.")
    music_mood: str = Field(description="One of: warm, confident, industrial, uplifting, calm.")
    caption: str = Field(description="The social caption. No trailing period.")
    hashtags: list[str] = Field(description="3-6 on-brand hashtags, each starting with '#'.")

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.scenes)

    @property
    def spoken_text(self) -> str:
        return " ".join(s.dialogue for s in self.scenes)


class Brief(BaseModel):
    """A WhatsApp request parsed into resolvable parts."""

    character: str | None = Field(description="Character name if one was named, else null.")
    product: str | None = Field(description="Product mentioned, verbatim, else null.")
    angle: str = Field(description="What the video should be about, in one line.")
    must_say: list[str] = Field(description="Phrases the operator explicitly asked for.")
    target_seconds: int = Field(description="Requested length; 30 if unstated.")


# --------------------------------------------------------------------------- #
# edit spec — the free, deterministic layer
# --------------------------------------------------------------------------- #


class Cut(BaseModel):
    scene: int
    clip_url: str
    start: float = 0.0  # trim from the clip's head
    end: float | None = None  # None = to the end of the clip


class MusicBed(BaseModel):
    track: str | None = None
    gain_db: float = -17.0
    duck_db: float = -9.0  # how far music drops under the voice


class Captions(BaseModel):
    # The approved reference video had NO burned captions; default off.
    enabled: bool = False
    style: str = "poppins-white-lower"


class EditSpec(BaseModel):
    """Everything about the finished cut except the generated pixels."""

    version: int = 1
    cuts: list[Cut]
    voiceover_url: str | None = None
    music: MusicBed = MusicBed()
    captions: Captions = Captions()
    end_slide_seconds: float = 3.0
    loudness_lufs: float = -14.0

    @property
    def content_seconds(self) -> float:
        return sum((c.end or 0.0) - c.start for c in self.cuts)


class EditClass(StrEnum):
    """Which layer a piece of feedback touches — cheapest that can satisfy it."""

    post = "post"  # re-cut only: trim, reorder, music, captions      $0
    audio = "audio"  # re-speak a line                                 pennies
    scene = "scene"  # regenerate ONE scene                            a few $
    script = "script"  # rewrite the screenplay                        scoped
    approve = "approve"  # not an edit at all
    cancel = "cancel"


class EditDecision(BaseModel):
    edit_class: EditClass = Field(description="The cheapest layer that can satisfy the request.")
    scene_idx: int | None = Field(description="Scene number if the change targets one scene.")
    instruction: str = Field(description="What to change, restated precisely for the next step.")
    reply: str = Field(description="One short line telling the operator what will happen.")
