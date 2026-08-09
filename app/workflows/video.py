"""The video workflow: a WhatsApp sentence becomes a published video.

The shape, and why it is this shape:

    brief -> script -> [APPROVE] -> keyframes+voice -> clips -> cut
          -> preview -> [vibe-edit loop] -> [APPROVE] -> publish

Two approval gates, deliberately placed. The first is before any generation
spend, so a script that is wrong costs nothing to fix. The second is before
anything reaches the public, which is contractual — nothing publishes without a
human saying yes.

The vibe-edit loop is the part that stops the usual fix-A-break-B spiral:
feedback is classified into the cheapest layer that can satisfy it, and only
that layer is touched. Re-cutting is free; only "this scene looks wrong" costs
money, and only for that scene.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.ai.client import generate_structured
from app.db import posts as posts_db
from app.db import storage
from app.db import videos as videos_db
from app.logging_config import get_logger
from app.messaging import twilio_client
from app.video import assembly, captions, keyframes, library, providers, voices
from app.video import script as script_engine
from app.video.models import (
    Brief,
    Captions,
    Cut,
    EditClass,
    EditDecision,
    EditSpec,
    SceneKind,
    VideoScript,
)

log = get_logger("app.workflows.video")

# Rough, deliberately conservative unit costs so the operator is never surprised.
COST_SPEAKING = 6.0
COST_BROLL = 3.0
COST_KEYFRAME = 0.05

_END_SLIDE_TAGLINE = "Feeding the globe since 1993"


# --------------------------------------------------------------------------- #
# resolving the brief
# --------------------------------------------------------------------------- #


@dataclass
class Resolved:
    brief: Brief
    character: library.Character | None
    product: library.Product | None
    question: str | None = None  # set when we must ask before proceeding


async def resolve(message: str) -> Resolved:
    """Turn a sentence into people and things, asking rather than guessing."""
    brief = await script_engine.parse_brief(message)

    character = None
    if brief.character:
        character = library.resolve_character(brief.character, usable_only=True)
        if character is None:
            names = ", ".join(c.name for c in library.load_characters() if c.usable)
            return Resolved(
                brief,
                None,
                None,
                question=(
                    f"I don't have anyone called “{brief.character}”. "
                    f"Available: {names}. Who should present it?"
                ),
            )

    product = None
    if brief.product:
        matches = library.match_products(brief.product)
        if len(matches) > 1:
            options = "\n".join(f"• {p.name}" for p in matches[:6])
            return Resolved(
                brief,
                character,
                None,
                question=f"Which product did you mean?\n{options}",
            )
        product = matches[0] if matches else None

    return Resolved(brief, character, product)


# --------------------------------------------------------------------------- #
# script stage
# --------------------------------------------------------------------------- #


def script_preview(doc: VideoScript, character: library.Character | None, cost: float) -> str:
    """The script as something readable on a phone — never JSON."""
    lines = [f"🎬 *{doc.title}*", f"~{doc.total_seconds:.0f}s · {len(doc.scenes)} scenes", ""]
    for scene in doc.scenes:
        who = character.name if (character and scene.kind is SceneKind.speaking) else "VO"
        lines.append(f"*{scene.idx}* ({scene.seconds:.0f}s) {scene.setting} — {scene.action}")
        lines.append(f'🗣 {who}: "{scene.dialogue}"')
    lines += [
        "",
        f"🎵 {doc.music_mood}",
        f"📝 {doc.caption}",
        " ".join(doc.hashtags),
        "",
        f"Reply *approve* to build it (about ${cost:.0f}, ~10 min), or tell me what to change.",
    ]
    return "\n".join(lines)


def estimate_cost(doc: VideoScript) -> float:
    speaking = sum(1 for s in doc.scenes if s.kind is SceneKind.speaking)
    broll = len(doc.scenes) - speaking
    return round(speaking * COST_SPEAKING + broll * COST_BROLL + len(doc.scenes) * COST_KEYFRAME, 2)


async def start(phone: str, message: str) -> str | None:
    """Begin a video: resolve, write the script, send it for approval."""
    resolved = await resolve(message)
    if resolved.question:
        return resolved.question

    await twilio_client.send_text(phone, "🎬 Writing the script — about a minute…")

    doc, problems = await script_engine.write_script(
        resolved.brief, resolved.character, resolved.product
    )
    if problems:
        log.warning("script still has problems", extra={"problems": problems})

    video = videos_db.create(brief=message, requested_by=phone)
    video_id = str(video["id"])
    videos_db.patch_meta(
        video_id,
        script=doc.model_dump(mode="json"),
        script_version=1,
        character=resolved.character.slug if resolved.character else None,
        product=resolved.product.slug if resolved.product else None,
        target_seconds=resolved.brief.target_seconds,
        stage="script_review",
    )
    await twilio_client.send_text(
        phone, script_preview(doc, resolved.character, estimate_cost(doc))
    )
    return None


def load(
    video_id: str,
) -> tuple[dict, VideoScript, library.Character | None, library.Product | None]:
    row = posts_db.get(video_id) or {}
    meta = videos_db.meta(row)
    doc = VideoScript.model_validate(meta["script"])
    character = library.get_character(meta["character"]) if meta.get("character") else None
    product = library.get_product(meta["product"]) if meta.get("product") else None
    return meta, doc, character, product


async def revise_script(video_id: str, phone: str, feedback: str) -> None:
    meta, doc, character, product = load(video_id)
    await twilio_client.send_text(phone, "✏️ Rewriting…")
    revised, problems = await script_engine.revise(
        doc, feedback, character, product, int(meta.get("target_seconds") or 30)
    )
    summary = script_engine.diff_summary(doc, revised)
    videos_db.patch_meta(
        video_id,
        script=revised.model_dump(mode="json"),
        script_version=int(meta.get("script_version") or 1) + 1,
    )
    await twilio_client.send_text(phone, f"Changed: {summary}")
    await twilio_client.send_text(phone, script_preview(revised, character, estimate_cost(revised)))
    if problems:
        log.warning("revision has problems", extra={"problems": problems})


# --------------------------------------------------------------------------- #
# production
# --------------------------------------------------------------------------- #


def _scene_hash(video_id: str, scene_dict: dict, keyframe_url: str, audio_url: str | None) -> str:
    """Identity of a scene's inputs — the key that stops us paying twice."""
    payload = f"{scene_dict}|{keyframe_url}|{audio_url or ''}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


async def _voice_for(video_id: str, doc: VideoScript, character: library.Character | None):
    """Speak every line; return per-scene audio URLs plus the full VO track.

    Audio first, always: the clip is cut to the words, so picture and sound
    cannot drift, and a re-read costs pennies without touching any video.
    """
    voice_id = character.voice_id if character else None
    if not voice_id:
        narrator = next((c for c in library.load_characters() if c.usable and c.voice_id), None)
        voice_id = narrator.voice_id if narrator else None
    if not voice_id:
        return {}, None, []

    per_scene: dict[int, str] = {}
    all_words: list[dict] = []
    chunks: list[bytes] = []
    offset = 0.0
    for scene in doc.scenes:
        audio, words = await asyncio.to_thread(voices.speak, voice_id, scene.dialogue)
        if not audio:
            continue
        # Lip-sync models reject MP3 (Higgsfield Speak returns invalid_audio_format),
        # so the URL handed to a provider is always WAV. The MP3 bytes are kept for
        # the assembled voiceover track.
        wav = await asyncio.to_thread(assembly.to_wav, audio)
        name, payload, ctype = (
            (f"vo_{scene.idx}.wav", wav, "audio/wav")
            if wav
            else (f"vo_{scene.idx}.mp3", audio, "audio/mpeg")
        )
        url = await asyncio.to_thread(storage.upload_video_asset, video_id, name, payload, ctype)
        per_scene[scene.idx] = url
        chunks.append(audio)
        for w in words:
            all_words.append({**w, "start": w["start"] + offset, "end": w["end"] + offset})
        offset += (words[-1]["end"] if words else scene.seconds) + 0.25
    return per_scene, chunks, all_words


async def _end_slide(path: Path) -> Path | None:
    """Render the official closing frame from HTML — never generated."""
    from app.templates.renderer import Renderer

    renderer = Renderer()
    try:
        await renderer.start()
        png = await renderer.render_file(
            "_end_slide.html",
            {"tagline": _END_SLIDE_TAGLINE},
            dimensions=(assembly.WIDTH, assembly.HEIGHT),
        )
    except Exception as exc:  # noqa: BLE001 — a missing end slide must not kill the video
        log.error("end slide failed", extra={"error": str(exc)})
        return None
    finally:
        await renderer.stop()
    path.write_bytes(png)
    return path


async def produce(video_id: str, phone: str) -> None:
    """Everything after script approval: keyframes, voice, clips, the cut, preview."""
    meta, doc, character, product = load(video_id)
    videos_db.patch_meta(video_id, stage="generating")

    await twilio_client.send_text(phone, "🎥 Building it. I'll send the cut when it's ready.")

    frames = await keyframes.render_all(video_id, doc.scenes, character, product)
    if not frames:
        await twilio_client.send_text(
            phone, "I couldn't build the opening frames. Nothing was charged — try again?"
        )
        videos_db.patch_meta(video_id, stage="failed")
        return
    await twilio_client.send_text(phone, f"🖼 Frames ready ({len(frames)}/{len(doc.scenes)})")

    audio_urls, audio_chunks, words = await _voice_for(video_id, doc, character)

    provider = providers.get_provider()
    known = videos_db.scene_artifacts(video_id)
    tmp = assembly.workdir()
    work = Path(tmp.name)

    async def one_scene(scene) -> tuple[int, str | None]:
        frame = frames.get(scene.idx)
        if frame is None:
            return scene.idx, None
        audio_url = audio_urls.get(scene.idx) if scene.kind is SceneKind.speaking else None
        key = _scene_hash(video_id, scene.model_dump(mode="json"), frame.url, audio_url)
        if key in known:
            log.info("scene reused", extra={"video": video_id, "scene": scene.idx})
            return scene.idx, known[key]

        motion = f"{scene.camera}. {scene.action}"
        if scene.kind is SceneKind.speaking and audio_url:
            result = await provider.speaking_scene(frame.url, audio_url, motion)
            cost = COST_SPEAKING
        else:
            result = await provider.broll_scene(frame.url, motion, scene.seconds)
            cost = COST_BROLL
        if not result.ok or not result.url:
            return scene.idx, None

        # Provider URLs expire, so take ownership of the bytes now. The local
        # copy is what assembly uses; hosting it is a cache for later re-cuts and
        # must never fail the video it belongs to.
        data = await providers.download(result.url)
        local = work / f"scene_{scene.idx}.mp4"
        local.write_bytes(data)
        videos_db.add_spend(video_id, cost)
        try:
            hosted = await asyncio.to_thread(
                storage.upload_video_asset, video_id, f"scene_{scene.idx}.mp4", data, "video/mp4"
            )
            videos_db.remember_artifact(video_id, key, hosted)
        except Exception as exc:  # noqa: BLE001 — cache miss is not a failure
            log.warning(
                "scene cache upload failed", extra={"scene": scene.idx, "err": str(exc)[:120]}
            )
            hosted = result.url
        return scene.idx, hosted

    results = await asyncio.gather(*(one_scene(s) for s in doc.scenes))
    clips = {idx: url for idx, url in results if url}
    local_clips = {
        idx: work / f"scene_{idx}.mp4" for idx in clips if (work / f"scene_{idx}.mp4").exists()
    }
    if not clips:
        await twilio_client.send_text(
            phone, "The clips didn't come back. I've stopped so nothing more is spent."
        )
        videos_db.patch_meta(video_id, stage="failed")
        return

    spec = EditSpec(
        cuts=[
            Cut(scene=s.idx, clip_url=clips[s.idx], end=s.seconds)
            for s in doc.scenes
            if s.idx in clips
        ],
        captions=Captions(enabled=False),
    )
    videos_db.patch_meta(
        video_id,
        clips=clips,
        edit_spec=spec.model_dump(mode="json"),
        words=words,
        audio_urls=audio_urls,
        edit_version=1,
        stage="video_review",
    )
    try:
        await assemble_and_preview(
            video_id, phone, spec, doc, audio_chunks, words, local_clips=local_clips
        )
        await _cache_scenes(video_id, local_clips)
    finally:
        tmp.cleanup()


def _get_row(video_id: str) -> dict:
    """posts_db.get narrowed for the thread bridge (a missing row is just empty)."""
    return posts_db.get(video_id) or {}


async def _cache_scenes(video_id: str, local_clips: dict[int, Path]) -> None:
    """Host the generated clips so a later re-cut costs nothing to reassemble.

    Runs after the operator already has their preview, and never raises: losing
    the cache only means a future re-cut has to re-fetch, which is annoying, not
    expensive — losing the video would be both.
    """
    for idx, path in local_clips.items():
        try:
            url = await asyncio.to_thread(
                storage.upload_video_asset,
                video_id,
                f"scene_{idx}.mp4",
                path.read_bytes(),
                "video/mp4",
            )
            row = await asyncio.to_thread(_get_row, video_id)
            clips = dict(videos_db.meta(row).get("clips") or {})
            clips[str(idx)] = url
            videos_db.patch_meta(video_id, clips=clips)
        except Exception as exc:  # noqa: BLE001
            log.warning("scene cache skipped", extra={"scene": idx, "err": str(exc)[:120]})


async def assemble_and_preview(
    video_id: str,
    phone: str,
    spec: EditSpec,
    doc: VideoScript,
    audio_chunks: list[bytes] | None,
    words: list[dict],
    *,
    local_clips: dict[int, Path] | None = None,
) -> None:
    """Cut the clips together and send the result for approval. Free to repeat."""
    meta = videos_db.meta(posts_db.get(video_id) or {})
    with assembly.workdir() as tmp:
        work = Path(tmp)
        paths: dict[int, Path] = {}
        for cut in spec.cuts:
            have = (local_clips or {}).get(cut.scene)
            if have and have.exists():
                paths[cut.scene] = have  # already on disk from generation
                continue
            data = await providers.download(cut.clip_url)
            p = work / f"scene_{cut.scene}.mp4"
            p.write_bytes(data)
            paths[cut.scene] = p

        vo_path = None
        if audio_chunks:
            vo_path = work / "vo.mp3"
            vo_path.write_bytes(b"".join(audio_chunks))
        elif meta.get("audio_urls"):
            vo_path = work / "vo.mp3"
            joined = b""
            for _, url in sorted(meta["audio_urls"].items(), key=lambda kv: int(kv[0])):
                joined += await providers.download(url)
            vo_path.write_bytes(joined)

        cap_path = None
        if spec.captions.enabled and words:
            cap_path = work / "captions.ass"
            cap_path.write_text(captions.to_ass(words), encoding="utf-8")

        slide = await _end_slide(work / "end.png")
        master = work / "master.mp4"
        ok = await assembly.render(
            spec,
            paths,
            master,
            voiceover=vo_path,
            end_slide=slide,
            captions=cap_path,
        )
        if not ok or not master.exists():
            await twilio_client.send_text(phone, "The assembly step failed — I'm looking at it.")
            return

        master_url = await asyncio.to_thread(
            storage.upload_video_asset,
            video_id,
            f"master_v{spec.version}.mp4",
            master.read_bytes(),
            "video/mp4",
        )
        preview = await assembly.compress_for_whatsapp(master, work / "preview.mp4")
        preview_url = master_url
        if preview and preview.exists():
            preview_url = await asyncio.to_thread(
                storage.upload_video_asset,
                video_id,
                f"preview_v{spec.version}.mp4",
                preview.read_bytes(),
                "video/mp4",
            )

        seconds = assembly.ffprobe_duration(master) or doc.total_seconds
    posts_db.set_image_url(video_id, master_url)
    posts_db.set_status(video_id, videos_db.VIDEO_REVIEW)
    videos_db.patch_meta(
        video_id,
        master_url=master_url,
        preview_url=preview_url,
        edit_spec=spec.model_dump(mode="json"),
        stage="video_review",
        caption=doc.caption,
        hashtags=doc.hashtags,
    )

    spend = videos_db.meta(posts_db.get(video_id) or {}).get("spend", 0.0)
    await twilio_client.send_media(phone, f"🎬 {doc.title}", preview_url, post_id=video_id)
    await twilio_client.send_text(
        phone,
        f"~{seconds:.0f}s · spent ${spend:.2f} so far\n\n"
        "Reply *approve* to publish, or tell me what to change "
        "(trim, reorder, music, a specific scene — I'll say what each costs first)",
    )


# --------------------------------------------------------------------------- #
# the vibe-edit loop
# --------------------------------------------------------------------------- #

_EDIT_SYSTEM = """You route feedback about a finished video to the cheapest layer that can satisfy it.

- post   : re-cut only — trim, shorten, reorder, drop a scene, music, captions,
           end-slide length. Costs nothing and takes a minute.
- audio  : the words or the delivery of a spoken line change, but the picture is fine.
- scene  : the PICTURE of one specific scene is wrong (looks odd, wrong angle,
           wrong setting, hands/faces wrong). Costs a few dollars, that scene only.
- script : the story, message or structure changes. Re-runs the affected scenes.
- approve: they are happy and want it published.
- cancel : they want to abandon it.

Always choose the cheapest class that genuinely fixes what they asked for.
"Make it shorter" is post, not script. "Say X instead of Y" is audio.
"Scene 2 looks weird" is scene. "Make it about export instead" is script.
Set scene_idx only when the change targets one numbered scene."""


async def classify_edit(feedback: str, doc: VideoScript) -> EditDecision:
    """Decide which layer the operator's words touch."""
    scenes = "\n".join(f'{s.idx}. ({s.kind}) {s.action} — "{s.dialogue}"' for s in doc.scenes)
    return await generate_structured(
        system=_EDIT_SYSTEM,
        user_content=f"The video has these scenes:\n{scenes}\n\nThe operator said:\n{feedback}",
        output_model=EditDecision,
        tool_name="emit_decision",
        tool_description="Classify the feedback and say what will happen.",
        max_tokens=500,
    )


def apply_post_edit(spec: EditSpec, decision: EditDecision, doc: VideoScript) -> EditSpec:
    """Re-cut without touching a generated pixel — the free layer.

    Handled here rather than by the model because these are arithmetic, and
    arithmetic should not be guessed.
    """
    text = decision.instruction.lower()
    cuts = list(spec.cuts)

    if decision.scene_idx and ("drop" in text or "remove" in text or "cut scene" in text):
        cuts = [c for c in cuts if c.scene != decision.scene_idx]
    elif "shorter" in text or "tighter" in text or "trim" in text:
        for c in cuts:
            if c.end:
                c.end = max(2.0, c.end * 0.85)
    elif "longer" in text and cuts:
        for c in cuts:
            if c.end:
                c.end = c.end * 1.15

    new = spec.model_copy(deep=True)
    new.cuts = cuts
    new.version = spec.version + 1
    if "caption" in text and ("add" in text or "on" in text or "subtitle" in text):
        new.captions.enabled = True
    if "no caption" in text or "remove caption" in text:
        new.captions.enabled = False
    return new


async def handle_video_feedback(video_id: str, phone: str, feedback: str) -> None:
    """The single entry point for anything said about a video under review."""
    meta, doc, character, product = load(video_id)
    stage = meta.get("stage")

    # Before the script is approved, everything is a script revision.
    if stage == "script_review":
        await revise_script(video_id, phone, feedback)
        return

    spec = EditSpec.model_validate(meta["edit_spec"])
    decision = await classify_edit(feedback, doc)
    await twilio_client.send_text(phone, decision.reply)

    if decision.edit_class is EditClass.post:
        new_spec = apply_post_edit(spec, decision, doc)
        videos_db.patch_meta(video_id, edit_spec=new_spec.model_dump(mode="json"))
        await assemble_and_preview(video_id, phone, new_spec, doc, None, meta.get("words") or [])
        return

    if decision.edit_class is EditClass.audio:
        await _redo_audio(video_id, phone, decision, doc, character, spec)
        return

    if decision.edit_class is EditClass.scene:
        await _redo_scene(video_id, phone, decision, doc, character, product, spec)
        return

    if decision.edit_class is EditClass.script:
        await revise_script(video_id, phone, feedback)
        videos_db.patch_meta(video_id, stage="script_review")
        return


async def _redo_audio(video_id, phone, decision, doc, character, spec) -> None:
    """Re-speak one line. The picture is untouched, so this costs pennies."""
    idx = decision.scene_idx or next((s.idx for s in doc.scenes), 1)
    scene = next((s for s in doc.scenes if s.idx == idx), None)
    if scene is None:
        return
    revised, _ = await script_engine.revise(
        doc, f"Scene {idx}: {decision.instruction}", character, None, int(doc.total_seconds)
    )
    videos_db.patch_meta(video_id, script=revised.model_dump(mode="json"))
    _, chunks, words = await _voice_for(video_id, revised, character)
    videos_db.patch_meta(video_id, words=words)
    await assemble_and_preview(video_id, phone, spec, revised, chunks, words)


async def _redo_scene(video_id, phone, decision, doc, character, product, spec) -> None:
    """Regenerate exactly one scene and splice it back in — never the whole video."""
    idx = decision.scene_idx
    scene = next((s for s in doc.scenes if s.idx == idx), None) if idx else None
    if scene is None:
        await twilio_client.send_text(phone, "Which scene number should I redo?")
        return

    await twilio_client.send_text(phone, f"🎬 Redoing scene {idx} (~${COST_SPEAKING:.0f})…")
    scene.action = f"{scene.action}. {decision.instruction}"
    frame = await keyframes.render_scene(video_id, scene, character, product)
    if frame is None:
        await twilio_client.send_text(phone, "That frame didn't come out — nothing charged.")
        return

    meta = videos_db.meta(posts_db.get(video_id) or {})
    provider = providers.get_provider()
    audio_url = (meta.get("audio_urls") or {}).get(str(idx)) or (meta.get("audio_urls") or {}).get(
        idx
    )
    motion = f"{scene.camera}. {scene.action}"
    if scene.kind is SceneKind.speaking and audio_url:
        result = await provider.speaking_scene(frame.url, audio_url, motion)
        cost = COST_SPEAKING
    else:
        result = await provider.broll_scene(frame.url, motion, scene.seconds)
        cost = COST_BROLL
    if not result.ok or not result.url:
        await twilio_client.send_text(phone, "The scene didn't generate. Try different wording?")
        return

    data = await providers.download(result.url)
    hosted = await asyncio.to_thread(
        storage.upload_video_asset, video_id, f"scene_{idx}_v2.mp4", data, "video/mp4"
    )
    videos_db.add_spend(video_id, cost)

    new_spec = spec.model_copy(deep=True)
    new_spec.version = spec.version + 1
    for cut in new_spec.cuts:
        if cut.scene == idx:
            cut.clip_url = hosted
    videos_db.patch_meta(video_id, edit_spec=new_spec.model_dump(mode="json"))
    await assemble_and_preview(video_id, phone, new_spec, doc, None, meta.get("words") or [])


# --------------------------------------------------------------------------- #
# approval
# --------------------------------------------------------------------------- #


async def approve(video_id: str, phone: str) -> None:
    """Approve: build it if the script was under review, publish if the cut was."""
    meta, doc, character, product = load(video_id)
    if meta.get("stage") == "script_review":
        await produce(video_id, phone)
        return
    await publish(video_id, phone)


async def publish(video_id: str, phone: str) -> None:
    """The only path to the public — and it is never reached without approval."""
    from app.publishing.blotato import blotato
    from app.publishing.platforms import Platform

    meta = videos_db.meta(posts_db.get(video_id) or {})
    master = meta.get("master_url")
    if not master:
        await twilio_client.send_text(phone, "There's no finished cut to publish yet.")
        return

    posts_db.set_status(video_id, "approved")
    await twilio_client.send_text(phone, "✅ Approved — publishing…")

    targets = [Platform.instagram, Platform.facebook, Platform.linkedin]
    results = await blotato.publish(
        master, meta.get("caption") or "", meta.get("hashtags") or [], targets
    )
    ok = [p.value for p, r in results.items() if r.success]
    failed = [f"{p.value} ({r.error})" for p, r in results.items() if not r.success]

    if ok:
        posts_db.set_status(video_id, "published")
    videos_db.patch_meta(
        video_id,
        stage="published",
        publish_results={p.value: r.success for p, r in results.items()},
    )

    lines = ["📤 Published: " + (", ".join(ok) if ok else "nothing")]
    if failed:
        lines.append("Didn't go out: " + "; ".join(failed))
        lines.append("Reply *retry <platform>* to try again.")
    await twilio_client.send_text(phone, "\n".join(lines))


async def cancel(video_id: str, phone: str) -> None:
    spend = videos_db.meta(posts_db.get(video_id) or {}).get("spend", 0.0)
    posts_db.set_status(video_id, "cancelled")
    videos_db.patch_meta(video_id, stage="cancelled")
    note = f" ${spend:.2f} was already spent on it." if spend else " Nothing was charged."
    await twilio_client.send_text(phone, f"Cancelled.{note}")


# --------------------------------------------------------------------------- #
# WhatsApp entry points
# --------------------------------------------------------------------------- #


async def begin(phone: str, request: str) -> None:
    """Handle a new video request from a message."""
    from app.messaging import conversation

    question = await start(phone, request)
    if question:
        await twilio_client.send_text(phone, question)
        await conversation.transition(
            phone,
            state=conversation.ConversationState.AWAITING_CLARIFICATION,
            context_patch={"pending_video_request": request},
        )
        return
    await conversation.transition(phone, state=conversation.ConversationState.VIDEO)


async def handle_while_in_video(phone: str, body: str, intent_type: str) -> bool:
    """Route a message that arrived while a video is in play.

    Returns False when there is no live video, so the caller falls back to the
    normal post flow rather than swallowing the message.
    """
    from app.messaging import conversation

    row = await asyncio.to_thread(lambda: videos_db.latest_for(phone))
    if row is None:
        await conversation.transition(phone, state=conversation.ConversationState.IDLE)
        return False

    video_id = str(row["id"])
    stage = videos_db.meta(row).get("stage")

    if stage == "generating":
        await twilio_client.send_text(
            phone, "Still building that one — I'll send it the moment it's ready."
        )
        return True

    if intent_type == "approval":
        await approve(video_id, phone)
        fresh = await asyncio.to_thread(lambda: posts_db.get(video_id))
        meta = videos_db.meta(fresh or {})
        if meta.get("stage") in ("published", "cancelled", "failed"):
            await conversation.transition(phone, state=conversation.ConversationState.IDLE)
        return True

    if intent_type == "cancellation":
        await cancel(video_id, phone)
        await conversation.transition(phone, state=conversation.ConversationState.IDLE)
        return True

    await handle_video_feedback(video_id, phone, body)
    return True
