"""Assembly: an edit spec plus generated clips -> the finished MP4.

This is the free half of the engine. Generation is expensive and random;
everything here is cheap and repeatable, so as much as possible lives on this
side of the line — cuts, voiceover, music, captions and the end slide are all
re-renderable in about a minute for nothing.

That split is what makes "make it shorter", "different music" and "drop the last
line" cost zero: none of them touch a generated pixel.

The end slide is rendered from the project's own HTML template, so the logo and
brand colours are pixel-exact rather than generated — the same rule the static
post templates follow.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.logging_config import get_logger
from app.video.models import EditSpec

log = get_logger("app.video.assembly")

WIDTH, HEIGHT = 1080, 1920
FPS = 30
_FFMPEG_TIMEOUT_S = 900

# One master encode that satisfies Instagram Reels, Facebook Reels and LinkedIn:
# H.264 High + AAC 48kHz stereo, closed GOP, yuv420p, moov atom at the front.
_VIDEO_ARGS = [
    "-c:v",
    "libx264",
    "-profile:v",
    "high",
    "-pix_fmt",
    "yuv420p",
    "-r",
    str(FPS),
    "-g",
    str(FPS * 2),
    "-keyint_min",
    str(FPS * 2),
    "-sc_threshold",
    "0",
    "-b:v",
    "8M",
    "-maxrate",
    "12M",
    "-bufsize",
    "16M",
    "-movflags",
    "+faststart",
]
_AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_duration(path: Path) -> float:
    """Length of a media file in seconds; 0.0 if it cannot be read."""
    probe = shutil.which("ffprobe")
    if not probe:
        return 0.0
    try:
        out = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return float((out.stdout or "0").strip() or 0.0)
    except (ValueError, subprocess.SubprocessError):
        return 0.0


def _scale_filter(label_in: str, label_out: str) -> str:
    """Fill 1080x1920 without distortion: scale to cover, then centre-crop."""
    return (
        f"[{label_in}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}[{label_out}]"
    )


def build_args(
    spec: EditSpec,
    clip_paths: dict[int, Path],
    out_path: Path,
    *,
    voiceover: Path | None = None,
    music: Path | None = None,
    end_slide: Path | None = None,
    captions: Path | None = None,
) -> list[str]:
    """The ffmpeg argv for one cut. Pure function — unit-testable without running it."""
    inputs: list[str] = []
    filters: list[str] = []
    segments: list[str] = []

    idx = 0
    for cut in spec.cuts:
        path = clip_paths.get(cut.scene)
        if path is None:
            continue
        args = ["-i", str(path)]
        if cut.start:
            args = ["-ss", f"{cut.start:.3f}", *args]
        if cut.end is not None:
            args = ["-t", f"{max(0.1, cut.end - cut.start):.3f}", *args]
        inputs.extend(args)
        filters.append(_scale_filter(f"{idx}:v", f"v{idx}"))
        segments.append(f"[v{idx}]")
        idx += 1

    if end_slide is not None:
        # A still image becomes a short tail clip; -loop/-t belong before -i.
        inputs.extend(["-loop", "1", "-t", f"{spec.end_slide_seconds:.2f}", "-i", str(end_slide)])
        filters.append(_scale_filter(f"{idx}:v", f"v{idx}"))
        segments.append(f"[v{idx}]")
        idx += 1

    if not segments:
        raise ValueError("no clips to assemble")

    filters.append(f"{''.join(segments)}concat=n={len(segments)}:v=1:a=0[vcat]")
    video_label = "vcat"

    if captions is not None:
        filters.append(f"[vcat]subtitles='{captions.as_posix()}'[vsub]")
        video_label = "vsub"

    vo_idx = music_idx = None
    if voiceover is not None:
        inputs.extend(["-i", str(voiceover)])
        vo_idx = idx
        idx += 1
    if music is not None:
        inputs.extend(["-stream_loop", "-1", "-i", str(music)])
        music_idx = idx
        idx += 1

    audio_label = None
    if vo_idx is not None and music_idx is not None:
        # Music ducks under the voice automatically via sidechain compression,
        # so the read is always intelligible without hand-tuned volume curves.
        filters.append(f"[{vo_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000[vo]")
        filters.append(
            f"[{music_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000,"
            f"volume={spec.music.gain_db}dB[mus]"
        )
        filters.append("[vo]asplit=2[vo1][vokey]")
        filters.append(
            "[mus][vokey]sidechaincompress=threshold=0.03:ratio=12:attack=20:"
            "release=400:makeup=1[musduck]"
        )
        filters.append("[vo1][musduck]amix=inputs=2:duration=first:dropout_transition=0[amix]")
        # apad for the same reason as the voice-only branch: the picture, not the
        # voiceover, decides how long the video is.
        filters.append(f"[amix]loudnorm=I={spec.loudness_lufs}:TP=-1.5:LRA=11,apad[aout]")
        audio_label = "aout"
    elif vo_idx is not None:
        # apad before -shortest, or the finished video is truncated to the length
        # of the voiceover and the end slide is cut off. Silence is padded to the
        # picture instead, and -shortest then stops at the last frame of video.
        filters.append(f"[{vo_idx}:a]loudnorm=I={spec.loudness_lufs}:TP=-1.5:LRA=11,apad[aout]")
        audio_label = "aout"
    elif music_idx is not None:
        filters.append(
            f"[{music_idx}:a]volume={spec.music.gain_db}dB,"
            f"loudnorm=I={spec.loudness_lufs}:TP=-1.5:LRA=11[aout]"
        )
        audio_label = "aout"

    args = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{video_label}]",
    ]
    if audio_label:
        args += ["-map", f"[{audio_label}]", *_AUDIO_ARGS, "-shortest"]
    else:
        args += ["-an"]
    args += [*_VIDEO_ARGS, str(out_path)]
    return args


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S, check=False
        )
    except subprocess.SubprocessError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or "")[-1200:]
    return True, ""


async def render(
    spec: EditSpec,
    clip_paths: dict[int, Path],
    out_path: Path,
    *,
    voiceover: Path | None = None,
    music: Path | None = None,
    end_slide: Path | None = None,
    captions: Path | None = None,
) -> bool:
    """Assemble the cut. Returns False (never raises) so callers can degrade."""
    if not ffmpeg_path():
        log.error("ffmpeg not available")
        return False
    try:
        args = build_args(
            spec,
            clip_paths,
            out_path,
            voiceover=voiceover,
            music=music,
            end_slide=end_slide,
            captions=captions,
        )
    except ValueError as exc:
        log.error("cannot assemble", extra={"error": str(exc)})
        return False
    ok, err = await asyncio.to_thread(_run, args)
    if not ok:
        log.error("ffmpeg failed", extra={"error": err[-400:]})
    return ok


async def compress_for_whatsapp(src: Path, dst: Path, *, max_mb: float = 15.0) -> Path | None:
    """A preview small enough to deliver over WhatsApp (documented ceiling 16MB).

    Steps down the bitrate until it fits rather than guessing once, because a
    preview that fails to send is worse than one that looks slightly softer.
    """
    if not ffmpeg_path():
        return None
    for scale, bitrate in ((720, "2M"), (720, "1200k"), (540, "900k"), (480, "600k")):
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"scale={scale}:-2",
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            "2M",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ar",
            "48000",
            str(dst),
        ]
        ok, err = await asyncio.to_thread(_run, args)
        if ok and dst.exists() and dst.stat().st_size <= max_mb * 1024 * 1024:
            return dst
        if not ok:
            log.error("preview encode failed", extra={"error": err[-300:]})
            return None
    log.warning("preview still over the size limit after the lowest setting")
    return dst if dst.exists() else None


def workdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="globex-video-")
