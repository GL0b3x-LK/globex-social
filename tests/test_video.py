"""VHS video pipeline tests.

`build_args` is a pure function (always tested). `composite_vhs` is tested for the
no-ffmpeg degrade path always, and end-to-end only when ffmpeg is installed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.messaging import video

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_build_args_scales_crops_overlays_and_caps() -> None:
    args = video.build_args("in.mp4", "ov.png", "out.mp4", width=1080, height=1920, max_seconds=45)
    joined = " ".join(args)
    assert args[0] == "ffmpeg" and args[-1] == "out.mp4"
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in joined
    assert "crop=1080:1920" in joined
    assert "overlay=0:0" in joined
    assert "-t 45" in joined
    assert "libx264" in joined and "yuv420p" in joined
    assert "0:a?" in joined  # keep audio if present


def test_build_args_grain_toggle() -> None:
    assert "noise=" in " ".join(video.build_args("i", "o", "x", grain=True))
    assert "noise=" not in " ".join(video.build_args("i", "o", "x", grain=False))


async def test_composite_returns_none_without_ffmpeg(monkeypatch) -> None:
    monkeypatch.setattr(video, "ffmpeg_path", lambda: None)
    assert await video.composite_vhs(b"video", b"overlay") is None


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")
async def test_composite_real_roundtrip_produces_mp4() -> None:
    ff = shutil.which("ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "c.mp4"
        overlay = Path(td) / "o.png"
        subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=12:duration=1",
             "-pix_fmt", "yuv420p", str(clip)],
            check=True, capture_output=True,
        )
        subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:duration=1",
             "-frames:v", "1", str(overlay)],
            check=True, capture_output=True,
        )
        out = await video.composite_vhs(clip.read_bytes(), overlay.read_bytes(), max_seconds=2)
    assert out is not None
    assert b"ftyp" in out[:64]  # valid MP4 container
