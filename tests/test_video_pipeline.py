"""The video pipeline: script rules, assembly arguments, edit routing, cost.

These cover the guarantees that cost money or credibility when they break — a
script that says a forbidden thing, an edit that regenerates video it didn't
need to, a cut that silently drops the end slide.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.video import assembly, captions, providers
from app.video import script as script_engine
from app.video.models import (
    Captions,
    Cut,
    EditClass,
    EditDecision,
    EditSpec,
    Scene,
    SceneKind,
    VideoMode,
    VideoScript,
)
from app.workflows import video as flow


def _scene(
    idx=1, kind=SceneKind.speaking, seconds=6.0, dialogue="Quality Control signs off"
) -> Scene:
    return Scene(
        idx=idx,
        kind=kind,
        seconds=seconds,
        setting="factory floor",
        camera="handheld, slow push-in",
        action="holding the sealed carton",
        dialogue=dialogue,
        delivery="calm, measured",
        keyframe_prompt="factory floor, packing line behind",
        beats=["holds the carton up", "turns it to camera", "sets it down"],
        motion_prompt=(
            "Handheld, slight natural sway. Begin chest-up as he lifts the carton "
            "into frame, ease a few inches closer over three seconds, settle as he "
            "lowers it to the bench and looks to camera."
        ),
    )


def _script(**over) -> VideoScript:
    base = dict(
        title="Duck shipping",
        mode=VideoMode.presenter,
        scenes=[_scene(), _scene(idx=2, kind=SceneKind.broll, dialogue="Sealed and chilled")],
        music_mood="warm",
        caption="From packing line to port",
        hashtags=["#GlobexInternational"],
    )
    base.update(over)
    return VideoScript(**base)


# --------------------------------------------------------------------------- #
# script rules
# --------------------------------------------------------------------------- #


def test_clean_script_passes() -> None:
    assert script_engine.lint(_script()) == []


def test_forbidden_claims_are_caught_in_dialogue() -> None:
    doc = _script(scenes=[_scene(dialogue="Our plants are Halal certified")])
    problems = script_engine.lint(doc)
    assert any("Halal" in p for p in problems)


def test_forbidden_claims_are_caught_in_the_caption() -> None:
    doc = _script(caption="We ship to 90+ countries")
    assert any("90+ countries" in p for p in script_engine.lint(doc))


def test_trailing_period_in_caption_is_rejected() -> None:
    doc = _script(caption="From packing line to port.")
    assert any("trailing period" in p for p in script_engine.lint(doc))


def test_dialogue_that_cannot_fit_its_scene_is_rejected() -> None:
    """An approved 30s script must actually run 30s, not 48s."""
    long_line = " ".join(["word"] * 60)
    doc = _script(scenes=[_scene(seconds=5.0, dialogue=long_line)])
    problems = script_engine.lint(doc)
    assert any("words but only" in p for p in problems)


def test_rejected_visual_effects_are_caught() -> None:
    scene = _scene()
    scene.action = "steam rising from the line under a spotlight"
    assert any("steam" in p for p in script_engine.lint(_script(scenes=[scene])))


def test_presenter_mode_needs_someone_speaking() -> None:
    doc = _script(scenes=[_scene(kind=SceneKind.broll)])
    assert any("speaking" in p for p in script_engine.lint(doc))


def test_voiceover_mode_forbids_speaking_scenes() -> None:
    doc = _script(mode=VideoMode.voiceover)
    assert any("Voiceover mode" in p for p in script_engine.lint(doc))


def test_total_length_respects_the_platform_cap() -> None:
    """Facebook only accepts API video as a Reel: 3-90 seconds."""
    scenes = [_scene(idx=i, seconds=12.0, dialogue="Short line") for i in range(1, 9)]
    assert any("exceeds" in p for p in script_engine.lint(_script(scenes=scenes)))


def test_renumber_fixes_model_numbering() -> None:
    doc = _script(scenes=[_scene(idx=7), _scene(idx=3, kind=SceneKind.broll)])
    assert [s.idx for s in script_engine.renumber(doc).scenes] == [1, 2]


def test_diff_summary_names_what_changed() -> None:
    a = _script()
    b = _script(music_mood="industrial")
    assert "music" in script_engine.diff_summary(a, b)
    assert script_engine.diff_summary(a, _script()) == "nothing substantive"


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #


def _spec(**over) -> EditSpec:
    base = dict(cuts=[Cut(scene=1, clip_url="u1", end=6.0), Cut(scene=2, clip_url="u2", end=5.0)])
    base.update(over)
    return EditSpec(**base)


def _paths(tmp_path: Path) -> dict[int, Path]:
    out = {}
    for i in (1, 2):
        p = tmp_path / f"s{i}.mp4"
        p.write_bytes(b"x")
        out[i] = p
    return out


def test_assembly_concats_every_clip_plus_the_end_slide(tmp_path: Path) -> None:
    slide = tmp_path / "end.png"
    slide.write_bytes(b"x")
    args = assembly.build_args(_spec(), _paths(tmp_path), tmp_path / "o.mp4", end_slide=slide)
    graph = args[args.index("-filter_complex") + 1]
    assert "concat=n=3" in graph  # two scenes + the end slide
    assert "+faststart" in args  # moov atom up front, required by the platforms


def test_every_clip_is_normalised_to_vertical(tmp_path: Path) -> None:
    args = assembly.build_args(_spec(), _paths(tmp_path), tmp_path / "o.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert graph.count(f"crop={assembly.WIDTH}:{assembly.HEIGHT}") == 2


def test_music_ducks_under_the_voiceover(tmp_path: Path) -> None:
    vo, music = tmp_path / "vo.mp3", tmp_path / "m.mp3"
    vo.write_bytes(b"x")
    music.write_bytes(b"x")
    args = assembly.build_args(
        _spec(), _paths(tmp_path), tmp_path / "o.mp4", voiceover=vo, music=music
    )
    graph = args[args.index("-filter_complex") + 1]
    assert "sidechaincompress" in graph
    assert "loudnorm=I=-14.0" in graph


def test_a_cut_with_no_clips_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        assembly.build_args(_spec(cuts=[]), {}, tmp_path / "o.mp4")


def test_missing_clip_is_skipped_not_faked(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    del paths[2]
    args = assembly.build_args(_spec(), paths, tmp_path / "o.mp4")
    assert "concat=n=1" in args[args.index("-filter_complex") + 1]


def test_trim_becomes_seek_and_duration(tmp_path: Path) -> None:
    spec = _spec(cuts=[Cut(scene=1, clip_url="u", start=1.5, end=4.0)])
    args = assembly.build_args(spec, _paths(tmp_path), tmp_path / "o.mp4")
    assert "-ss" in args and "1.500" in args
    assert "2.500" in args  # end - start


# --------------------------------------------------------------------------- #
# captions
# --------------------------------------------------------------------------- #


def test_captions_group_into_short_phrases() -> None:
    words = [{"word": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(12)]
    lines = captions.group_words(words, max_words=5)
    assert len(lines) == 3
    assert lines[0]["start"] == 0.0


def test_caption_file_is_valid_ass_with_the_brand_font() -> None:
    words = [{"word": "Quality", "start": 0.0, "end": 0.4}]
    doc = captions.to_ass(words)
    assert "[Events]" in doc and "Poppins" in doc
    assert "Dialogue: 0,0:00:00.00,0:00:00.40" in doc


def test_captions_default_off() -> None:
    """The approved reference video carried no burned captions."""
    assert Captions().enabled is False
    assert captions.wanted(_spec()) is False


# --------------------------------------------------------------------------- #
# provider behaviour
# --------------------------------------------------------------------------- #


def test_duration_bucket_never_undershoots_a_scene() -> None:
    assert providers.duration_bucket(4.0) == "5"
    assert providers.duration_bucket(5.5) == "5"
    assert providers.duration_bucket(6.0) == "10"  # overshoot is trimmed for free


# --------------------------------------------------------------------------- #
# the vibe-edit loop
# --------------------------------------------------------------------------- #


def _decision(cls: EditClass, instruction: str, idx: int | None = None) -> EditDecision:
    return EditDecision(edit_class=cls, scene_idx=idx, instruction=instruction, reply="ok")


def test_dropping_a_scene_is_a_free_recut() -> None:
    spec = _spec()
    out = flow.apply_post_edit(spec, _decision(EditClass.post, "drop scene 2", 2), _script())
    assert [c.scene for c in out.cuts] == [1]
    assert out.version == spec.version + 1


def test_shorter_trims_rather_than_regenerating() -> None:
    spec = _spec()
    out = flow.apply_post_edit(spec, _decision(EditClass.post, "make it tighter"), _script())
    assert all(c.end < 6.01 for c in out.cuts)
    assert [c.clip_url for c in out.cuts] == [c.clip_url for c in spec.cuts]  # same pixels


def test_turning_captions_on_is_a_free_recut() -> None:
    out = flow.apply_post_edit(_spec(), _decision(EditClass.post, "add captions"), _script())
    assert out.captions.enabled is True


def test_cost_estimate_prices_speaking_above_broll() -> None:
    speaking_only = _script(scenes=[_scene(), _scene(idx=2)])
    broll_only = _script(
        mode=VideoMode.voiceover,
        scenes=[_scene(kind=SceneKind.broll), _scene(idx=2, kind=SceneKind.broll)],
    )
    assert flow.estimate_cost(speaking_only) > flow.estimate_cost(broll_only)


def test_scene_hash_changes_only_when_inputs_change() -> None:
    """The never-pay-twice key: same inputs must never trigger a second charge."""
    scene = _scene().model_dump(mode="json")
    a = flow._scene_hash("v1", scene, "frame.jpg", "audio.mp3")
    assert a == flow._scene_hash("v1", scene, "frame.jpg", "audio.mp3")
    assert a != flow._scene_hash("v1", scene, "frame2.jpg", "audio.mp3")
    assert a != flow._scene_hash("v1", {**scene, "dialogue": "different"}, "frame.jpg", "audio.mp3")


def test_script_preview_reads_like_a_message_not_json() -> None:
    text = flow.script_preview(_script(), None, 18.0)
    assert "🎬" in text and "approve" in text
    assert "{" not in text and "scene_idx" not in text


def test_audio_is_padded_so_the_end_slide_is_never_cut(tmp_path: Path) -> None:
    """Without apad, -shortest truncates the video to the voiceover and the
    client-required end slide disappears from every video."""
    vo = tmp_path / "vo.mp3"
    vo.write_bytes(b"x")
    args = assembly.build_args(_spec(), _paths(tmp_path), tmp_path / "o.mp4", voiceover=vo)
    graph = args[args.index("-filter_complex") + 1]
    assert "apad" in graph
    assert "-shortest" in args  # picture length wins, audio is padded to match


# --------------------------------------------------------------------------- #
# provider selection
# --------------------------------------------------------------------------- #


def test_higgsfield_wins_when_configured(monkeypatch) -> None:
    """Higgsfield is the client's own account and the tool the approved video
    came from, so it takes precedence the moment credentials exist."""
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "higgsfield_api_key", "k", raising=False)
    monkeypatch.setattr(s, "higgsfield_api_secret", "s", raising=False)
    monkeypatch.setattr(s, "video_provider", None, raising=False)
    assert isinstance(providers.get_provider(), providers.HiggsfieldProvider)


def test_falls_back_to_kie_without_higgsfield_credentials(monkeypatch) -> None:
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "higgsfield_api_key", None, raising=False)
    monkeypatch.setattr(s, "higgsfield_api_secret", None, raising=False)
    monkeypatch.setattr(s, "video_provider", None, raising=False)
    assert isinstance(providers.get_provider(), providers.KieProvider)


def test_explicit_provider_choice_is_honoured(monkeypatch) -> None:
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "higgsfield_api_key", "k", raising=False)
    monkeypatch.setattr(s, "higgsfield_api_secret", "s", raising=False)
    monkeypatch.setattr(s, "video_provider", "kie", raising=False)
    assert isinstance(providers.get_provider(), providers.KieProvider)


def test_higgsfield_uses_the_models_the_account_actually_exposes() -> None:
    """Verified against the live /models list: Speak 2.0 for lip-sync, DoP for
    motion. Both take inputs this pipeline already produces."""
    hf = providers.HiggsfieldProvider()
    assert hf.SPEAKING_MODEL == "higgsfield-ai/speak"
    assert hf.AUDIO_PARAM == "audio_url"
    assert hf.BROLL_MODEL.startswith("higgsfield-ai/dop/")


def test_an_empty_wallet_is_reported_in_words_an_operator_can_act_on() -> None:
    """'not_enough_credits' means nothing to Ilan; the reply must say what to do."""
    resp = httpx.Response(400, json={"detail": "not_enough_credits"})
    msg = providers._friendly_error(resp)
    assert "out of credits" in msg and "top it up" in msg
    assert "nothing was charged" in msg


def test_speech_is_converted_to_wav_for_lipsync() -> None:
    """Higgsfield Speak rejects MP3 and M4A with invalid_audio_format (verified
    live), so the URL handed to a lip-sync model must be WAV."""
    import subprocess

    if not assembly.ffmpeg_path():
        pytest.skip("ffmpeg not installed")
    src = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "libmp3lame",
            "-f",
            "mp3",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    ).stdout
    wav = assembly.to_wav(src)
    assert wav is not None
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def test_the_generator_receives_the_directors_prompt_not_a_fragment() -> None:
    """The video model acts on motion_prompt. Sending "{camera}. {action}" gave it
    two stage fragments to work from, which is not direction."""
    scene = _scene()
    assert len(scene.motion_prompt) > 80
    assert scene.motion_prompt != f"{scene.camera}. {scene.action}"


def test_a_scene_without_direction_is_rejected() -> None:
    scene = _scene()
    scene.motion_prompt = ""
    assert any("shot direction" in p for p in script_engine.lint(_script(scenes=[scene])))


def test_a_scene_without_beats_is_rejected() -> None:
    scene = _scene()
    scene.beats = []
    assert any("beats" in p for p in script_engine.lint(_script(scenes=[scene])))


def test_rejected_visuals_are_caught_in_the_direction_too() -> None:
    """The no-steam rule has to hold in the prompt the model actually reads."""
    scene = _scene()
    scene.motion_prompt = "Slow push in as steam rises across the frame"
    assert any("steam" in p for p in script_engine.lint(_script(scenes=[scene])))
