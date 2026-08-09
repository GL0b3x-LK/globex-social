"""Word-timed captions as an ASS subtitle file.

ElevenLabs returns character-level timings with the audio, so caption timing is
exact rather than estimated and costs nothing extra. Rendering them as ASS (not
burned into a generated clip) keeps the $52 lesson intact: changing a caption is
a re-render, not a regeneration.

Off by default — the approved reference video carried no burned captions.
"""

from __future__ import annotations

from app.video.models import EditSpec

# Poppins is the client's caption face; white on a soft shadow reads on any
# footage without a coloured box, which they rejected as "PowerPointy".
_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Globex,Poppins,64,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,0,3,2,80,80,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_MAX_LINE_WORDS = 5  # short phrases read better on vertical video than sentences


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):d}:{int(m):02d}:{s:05.2f}"


def group_words(words: list[dict], max_words: int = _MAX_LINE_WORDS) -> list[dict]:
    """Chunk word timings into short caption phrases, breaking at punctuation."""
    lines: list[dict] = []
    current: list[dict] = []
    for word in words:
        current.append(word)
        ends_clause = word["word"].rstrip().endswith((".", ",", "?", "!", ";", ":", "—"))
        if len(current) >= max_words or ends_clause:
            lines.append(
                {
                    "text": " ".join(w["word"] for w in current),
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                }
            )
            current = []
    if current:
        lines.append(
            {
                "text": " ".join(w["word"] for w in current),
                "start": current[0]["start"],
                "end": current[-1]["end"],
            }
        )
    return lines


def to_ass(words: list[dict], *, offset: float = 0.0) -> str:
    """An ASS subtitle document for the given word timings."""
    body = []
    for line in group_words(words):
        text = line["text"].replace("\n", " ").strip()
        if not text:
            continue
        body.append(
            f"Dialogue: 0,{_ts(line['start'] + offset)},{_ts(line['end'] + offset)},"
            f"Globex,,0,0,0,,{text}"
        )
    return _HEADER + "\n".join(body) + "\n"


def wanted(spec: EditSpec) -> bool:
    return spec.captions.enabled
