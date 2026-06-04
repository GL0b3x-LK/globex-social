"""VHS video pipeline: composite the HUD overlay onto Karen's submitted video.

The overlay (REC dot, timecode, date stamp, scanlines) is rendered to a
transparent 9:16 PNG by the HTML renderer; here ffmpeg scales/crops her clip to
9:16, lays the overlay on top, adds light grain, and caps the length. Like the
rest of the media layer, failures degrade to None rather than raising.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from app.logging_config import get_logger

log = get_logger("app.messaging.video")

OUT_W, OUT_H = 1080, 1920  # 9:16 (reels / stories)
MAX_SECONDS = 60
_FFMPEG_TIMEOUT_S = 180


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def build_args(
    in_video: str,
    in_overlay: str,
    out_path: str,
    *,
    width: int = OUT_W,
    height: int = OUT_H,
    max_seconds: int = MAX_SECONDS,
    grain: bool = True,
) -> list[str]:
    """The ffmpeg argv: scale-to-fill+crop to 9:16, overlay the HUD, optional grain."""
    chain = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[bg];"
        f"[bg][1:v]overlay=0:0:format=auto[v1]"
    )
    if grain:
        chain += ";[v1]noise=alls=9:allf=t+u[vout]"
        out_label = "[vout]"
    else:
        out_label = "[v1]"
    return [
        "ffmpeg",
        "-y",
        "-i",
        in_video,
        "-i",
        in_overlay,
        "-filter_complex",
        chain,
        "-map",
        out_label,
        "-map",
        "0:a?",  # keep audio if present
        "-t",
        str(max_seconds),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        out_path,
    ]


async def composite_vhs(
    video_bytes: bytes, overlay_png: bytes, *, max_seconds: int = MAX_SECONDS
) -> bytes | None:
    """Burn the VHS overlay onto the video. Returns MP4 bytes, or None on failure."""
    exe = ffmpeg_path()
    if not exe:
        log.error("ffmpeg not found on PATH — cannot process video")
        return None
    with tempfile.TemporaryDirectory() as td:
        vin = Path(td) / "in"
        oin = Path(td) / "overlay.png"
        out = Path(td) / "out.mp4"
        vin.write_bytes(video_bytes)
        oin.write_bytes(overlay_png)
        args = build_args(str(vin), str(oin), str(out), max_seconds=max_seconds)
        args[0] = exe
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=_FFMPEG_TIMEOUT_S)
        except TimeoutError:
            log.error("ffmpeg timed out")
            return None
        except Exception as exc:  # noqa: BLE001 — never crash the background task
            log.error("ffmpeg invocation failed", extra={"error": str(exc)})
            return None
        if proc.returncode != 0 or not out.exists():
            tail = err.decode("utf-8", "ignore")[-400:] if err else ""
            log.error("ffmpeg returned error", extra={"code": proc.returncode, "stderr": tail})
            return None
        return out.read_bytes()
