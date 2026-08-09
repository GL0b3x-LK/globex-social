# Globex Video Engine — Goodwill Build Plan

> **Status (2026-08-09): BUILT.** The pipeline described below exists and runs — see the progress log. What remains is tuning and the operational items in §19, not construction. Companion to [globex-sm-automation-plan.md](globex-sm-automation-plan.md) (the contracted static system, which is code-complete and deployed). The video engine is the goodwill layer on top: same WhatsApp command center, same approval discipline, new medium.
>
> **For agentic workers:** read §1 (the rules we already paid to learn) before touching anything. Every architectural choice below traces back to either a client-approved artifact or a mistake that already cost money. Build phase-by-phase (§18); tick boxes; date the progress log.

---

## 0. What this is

Ilan (operator) or Karen messages the WhatsApp number:

> *"create a video of John holding our raw chicken and talking about our process of shipping it"*

The system:

1. Resolves **John** (a named character from the character library) and **raw chicken** (a product from the product library).
2. Opus writes the full screenplay — every scene, every spoken word, delivery notes, camera feel, timing — obeying the video brand bible.
3. Sends the script + storyboard keyframes to WhatsApp. Operator edits in plain English until the script is right. **No generation money is spent before this approval.**
4. On approval: per-character voice speaks the lines (TTS), Higgsfield animates each scene from locked keyframes, and a deterministic assembly pass adds music, the official end-slide, and (optionally) captions.
5. Preview video lands in WhatsApp. Operator "vibe edits" in plain English — but edits are routed to the *cheapest layer that can satisfy them* (post-edit → scene regen → script change), so we never re-roll a whole video to change one word.
6. On final approval: publishes via Blotato to IG/FB/LinkedIn — by default into the **Tue/Thu slots** the content calendar deliberately left empty (`calendar_source._POST_WEEKDAYS = (0, 2, 4)` reserved them), but creatable and postable any day.

Nothing auto-publishes. Ever. Same contract rule as the static system.

```
WhatsApp brief
  → resolve character + product (Supabase libraries)
  → Opus screenplay (structured, linted, timed)          ── $0.2
  → storyboard keyframes (nano-banana-2 multi-ref)       ── pennies
  → ✋ OPERATOR APPROVES STORYBOARD (the cost gate)
  → TTS per-character voice w/ timestamps (ElevenLabs)   ── pennies
  → per-scene video generation (Higgsfield)              ── $$ the expensive step
  → deterministic assembly: cuts + VO + music + end-slide ── $0
  → WhatsApp preview (≤16MB compress)
  → ✋ VIBE-EDIT LOOP (edits routed to cheapest layer)
  → ✋ FINAL APPROVAL
  → Blotato publish (Tue/Thu default) → log → done
```

---

## 1. The quality bar and the rules we already paid to learn

The bar is a specific file: **`ugc edited .mp4`** (approved 2026-07-30) — 29.7s, 1080×1920 vertical, real-footage factory UGC feel, professional voiceover, **no burned captions**, hard cuts, official logo end-slide. Every generated video should feel like a sibling of that file.

**Video brand bible** (distilled from the June 18 / June 30 / July 1 / July 22 feedback rounds — these are client rules, not suggestions):

| Rule | Source |
|---|---|
| 9:16, 1080×1920, ~30s target (60s hard cap) | approved video |
| Real-footage factory UGC feel; handheld authenticity; hard cuts, no fancy transitions | approved video |
| Professional voiceover tone — no casual avatars, no mid-video accent shifts | Len feedback |
| **NO steam/smoke/particles, no spotlight effects** | Len feedback |
| **NO graphic carcass shots** — packaged/plated presentation always wins | Len feedback (same rule the photo picker encodes) |
| **NO "Halal" in any script** | client instruction |
| "shipped globally", never "90+ countries" | client instruction |
| "Quality Control", never "inspected by hand" | client instruction |
| No trailing periods in on-screen text; "City, Country" format | Len |
| **Real product packaging** (e.g. the orange/green duck label) — never clear bags, never invented packaging | client feedback |
| Audience-matched presenters (e.g. Asian presenter for duck-market content) | client feedback |
| Poppins for any captions; colors #002D70 / #5BC0DE only | design bible |
| **End every video with the official end-slide** (top-lockup: GLOBEX – INTERNATIONAL / Feeding the globe since 1993) | design bible |
| Nothing "PowerPointy", no kitsch | Len |

**The $52 lesson (this is the architecture):** during the design rounds, regenerating a whole clip to change one word cost $52. The fix the team already converged on — *lock the source image + script, generate once, do all text/captions/end-slide in post so tweaks are free* — is exactly the layering this plan industrializes:

- **Generation layer** (expensive, non-deterministic): produces raw scene clips only. No text, no logo, no captions ever baked in.
- **Assembly layer** (free, deterministic): cuts, voiceover, music, captions, end-slide — all re-renderable in ~a minute for $0.

The user-facing consequence: "change the caption", "different music", "trim scene 2", "swap the end" are *free and fast*. Only "his hands look weird" costs a scene regen — one scene, never the whole video.

---

## 2. Two creation modes, one engine

The approved video contained **no on-camera speaker** — it was b-roll + voiceover. The new vision adds named characters speaking to camera. Both are first-class and share the entire pipeline; a script is just a list of scenes, each either `speaking` or `broll`:

- **Mode A — Presenter video:** one or more scenes feature a library character speaking to camera (lip-synced to their locked voice). *"John holding our raw chicken, talking about shipping."*
- **Mode B — Voiceover UGC:** zero speaking scenes; b-roll (AI-generated, or real factory footage from the asset library) under the narrator voiceover. This is *literally the approved video's format* — the safest default when no character is named.

Mode B is also the graceful degrade: if lipsync generation fails or looks off for a scene, that scene can fall back to b-roll + VO without changing the script's words.

---

## 3. The libraries: characters and products

**Storage decision: Supabase is the source of truth; Higgsfield holds only derived/cached generation assets.** Reasons:

1. The WhatsApp bot must resolve "John" → persona, reference images, voice id in milliseconds; that's a DB row, not a platform lookup.
2. The Higgsfield workspace is currently authenticated as **Sonder's account** (ownership unresolved — §17). A library trapped in the wrong workspace is a hostage; Supabase is already client-owned, like Railway.
3. Portability: if the generation provider changes (Seedance direct, Veo, whatever wins next quarter), the roster, voices, pack shots, and personas survive untouched.
4. Karen's team can *see* the roster in the Supabase dashboard — same reason Supabase won originally.

Higgsfield-side artifacts (uploaded reference sets, a persistent character/"Soul ID" if the API supports one) are stored as *pointers* on the Supabase row (`provider_refs jsonb`), treated as cache, rebuildable.

### 3.1 `characters` table

| column | notes |
|---|---|
| `id` uuid | |
| `name` text unique | "John" — what the operator types |
| `aliases` text[] | nicknames, misspellings observed in chat |
| `persona` text | one paragraph: personality, speaking style, energy — feeds the scriptwriter |
| `role` text | "QC lead on the factory floor" — grounds what they'd plausibly talk about |
| `gender`, `appearance_notes` | wardrobe locked to plain navy food-safety workwear (hairnet/smock realism per the approved footage). **No logo on clothing in-generation** — models mangle logos; the logo arrives in post, always |
| `market_tags` text[] | encodes audience-matching, e.g. `{duck, asia}` → the "Asian presenter for duck-market UGC" rule becomes data |
| `voice_id` text | ElevenLabs voice, designed once and **locked forever** — mechanically kills "mid-video accent shifts". **Every voice is neutral American English** (Abdul, 2026-08-09): Globex is a US company and accent variation was rejected in the design rounds. Personas stay distinct by age, register, pace and warmth — never by accent, and an accent is never inferred from appearance (`_meta.voice_rule`, test-enforced) |
| `reference_image_urls` text[] | **The identity itself.** 4 canonical shots (front, context, three-quarter, talking) generated ONCE and hosted public-read in Supabase Storage, front portrait first. Every keyframe passes these URLs to the generator as references — the `visual_prompt` is never re-run at video time, because re-prompting yields a different-looking person. A character with no stored references is mechanically unusable (`Character.usable`) |
| `provider_refs` jsonb | e.g. Higgsfield character id — cache, rebuildable |
| `is_real_person` bool, `likeness_consent` (bool, date, doc URL) | **hard block**: a real person cannot be a character without recorded consent. The 10 seed characters are invented personas so this is moot for them — but the day someone says "make a video of Alan with his hot sauce", this field is what stands between us and a likeness problem |
| `status` | draft / approved / retired — only `approved` characters are usable; **Len/Ilan approve each character sheet once, then it's locked** |

**Seeding — done 2026-08-09.** `app/data/characters.json` holds the roster the Aug 6 call specified, with Abdul's diversity spec applied: **10 personas, 5 male / 5 female, Asian + African + Caucasian represented in both genders, plus Latino and South Asian, ages spread 24–58** (John 58 Caucasian QC lead · Wei 34 East Asian export · Kwame 41 African cold-chain · Diego 27 Latino packing line · Arjun 46 South Asian logistics · Priya 24 South Asian QC · Grace 29 African QC · Anna 36 Caucasian warehouse · Sofia 44 Latina production · Mei 52 East Asian senior QC). Each carries a persona, speaking style, `visual_prompt`, `voice_direction`, setting affinity and market tags. All ship `status: "draft"` — **a draft character is mechanically unusable**, so nobody fronts the brand before Len approves the sheet. `scripts/generate_characters.py` renders each sheet (front portrait, three-quarter, waist-up talking, workplace context) from the persona data; the shared style block and the client's no-list (logos, steam, spotlights, carcasses) are built from the roster's own `_meta`, so prompts can't drift from the rules.

### 3.2 `products` table — doubles as the master product list that doesn't exist

(No master Globex product list exists anywhere — established 2026-08-08. This table becomes it, and the SKU ask to Ilan fills it.)

**Seeded as a skeleton 2026-08-09** — `app/data/products.json`, 10 entries covering everything we hold pack shots for (six chicken lines, two duck lines, the chicken export carton, the branded carton). Pork, beef, seafood and grains are added later; a new product is one JSON entry, never a code change.

| column | notes |
|---|---|
| `id`, `name`, `aliases` | "Whole Duck — Retail Carton"; aliases `{raw duck, duck carton}` — what people actually type |
| `category` | poultry / duck / pork / beef / seafood / grains… |
| `description`, `formats` | pack sizes, grades, export formats |
| `pack_shot_urls` text[] | **real photographs of real packaging** — the 17-photo `asset_pool` seeds this; the orange/green duck label rule becomes enforceable because the keyframe compositor receives the *actual* pack shot as a reference image |
| `talking_points` jsonb | provenance, QC process, logistics facts the scriptwriter may use — so scripts are grounded, not hallucinated |
| `claims_forbidden` text[] | per-product no-say list, seeded globally with `{Halal, 90+ countries, inspected by hand}` — the script linter (§6) enforces it deterministically |
| `visual_rules` jsonb | e.g. `{"never_unpackaged": true}` for raw proteins — the carcass rule as data |
| `markets` text[] | export destinations → presenter matching via `market_tags` |
| `status` | active / retired |

### 3.3 Library management from WhatsApp

- `"add a character"` → guided mini-intake (name → persona line → send 2–4 photos *or* "invent them: describe them" → character sheet preview → approve → voice generated + locked).
- `"add a product"` → name → send pack-shot photo(s) → category/description → done.
- `"list characters"` / `"list products"` → roster with one-line descriptions.
- `"retire John"` → status flip, never hard-delete (old videos reference him).

Also seedable/editable by us via script — WhatsApp management is for the client's independence, not the only path.

### 3.4 `broll_assets` table (real footage)

The approved video was *real factory footage*. Real clips beat generated ones for authenticity every time we have them. Operator (or Ilan via us) can send real clips → stored, tagged (`factory`, `port`, `packing-line`, product tags) → the scriptwriter and assembly layer prefer real b-roll over generated b-roll when tags match. This is the single cheapest way to exceed expectations: the engine gets *better* as Globex feeds it real footage, and pure-AI is only the fallback.

---

## 4. Conversational UX

### 4.1 New conversation states

`ConversationState` gains: `VIDEO_INTAKE`, `VIDEO_SCRIPT_REVIEW`, `VIDEO_GENERATING`, `VIDEO_REVIEW`. Routing stays pure data in `state_machine.py` (same exhaustively-tested table pattern). Intent classifier gains `new_video_request` (trigger: "video of…", "make a video…", "create a video…").

### 4.2 The flows

**Rich brief** (the example message) → skip intake, straight to script.
**Bare "make a video"** → guided intake, mirroring the 4-question static intake:

1. *Who's in it?* (roster list, or "no presenter — voiceover style" → Mode B)
2. *Which product?* (list; optional — brand videos exist)
3. *What's the story?* (the angle: shipping process, QC, trade-show promo…)
4. *Anything specific?* (length, market, CTA, must-say lines)

**Script preview message** (WhatsApp-native, readable, never JSON):

> 🎬 *Duck shipping, told by John* — ~32s, 4 scenes
>
> **1** (0–5s) Factory floor, handheld. John chest-up, holding the retail duck carton, label out.
> 🗣 "Every carton you see here has a plane to catch"
> **2** (5–13s) B-roll: packing line, cartons sealed and stacked.
> 🗣 (VO) "Sealed, chilled and certified the same day it's packed"
> **3** (13–24s) John at the loading dock, truck behind.
> 🗣 "Quality Control signs off before anything ships — no exceptions"
> **4** (24–29s) B-roll: port, containers. 🗣 (VO) "From our floor to your market — shipped globally"
> ➕ End-slide (3s)
> 🎵 Warm, confident · 📝 Caption: *From packing line to port …* #GlobexInternational
>
> Reply **approve** to build it (≈ $18, ≈ 12 min), or tell me what to change.

**Storyboard follows as images:** 2–4 composited keyframes so they *see* John holding the actual carton before any video money is spent. Script + storyboard approval is one merged gate by default (fewer round trips).

**During generation:** immediate ack with ETA, then stage pings ("voice done ✓", "scene 2/4 rendering…", "assembling…"). `status` answers anytime. State is locked — new requests queue politely.

**Video preview → vibe-edit loop** (§10) → **final approval** → publish or hold for the chosen date (approval-hold reuse — approving a Tuesday-dated video on Sunday holds it and it goes out Tuesday 09:00, exactly like static posts).

### 4.3 Coexistence with static posts

A static post and a video can both be pending for the same operator. Approvals are qualified when ambiguous: bare "approve" targets the most recently previewed item and the bot *says which* ("Approved the video ✓ — Tuesday's packaging post is still waiting; reply **approve post** for that one"). "approve video" / "approve post" always work explicitly. Same rule for "cancel".

---

## 5. Stage A — Brief parsing

Opus structured-output call (same forced-tool-use pattern as `GeneratedPost`) → `VideoBrief`: `character_id | null` (resolved against names+aliases, fuzzy), `product_id | null`, `angle`, `market`, `must_say[]`, `target_seconds`, `mode` (presenter/voiceover). Unresolvable references never guess: *"I don't have a 'Jon' yet — closest is John (QC lead). Use John, or add someone new?"*

---

## 6. Stage B — The screenplay (Opus)

`VideoScript` (Pydantic, versioned, immutable per revision):

```
title, target_seconds, character_id?, product_id?, market,
scenes[]: {
  idx, seconds, kind: speaking|broll,
  setting, camera,            # "factory floor, handheld, slow push-in"
  action,                     # what physically happens
  dialogue,                   # exact words (spoken by character or VO narrator)
  delivery,                   # tone notes for TTS: pace, warmth
  keyframe_prompt,            # composite instruction referencing character+product refs
  broll_pref: real|generated  # real asset-library footage wins when tagged match exists
},
music_mood, captions_enabled (default FALSE — the approved video had none),
caption, hashtags, cta
```

Prompt inputs: the video brand bible (§1) verbatim as hard constraints, character `persona`+`role`, product `talking_points`+`claims_forbidden`, and the approved video's structure as the exemplar (hook → process → credibility → end-slide).

**Two deterministic validators run after every generation and revision** (never trust the prompt alone):

1. **Claims/style linter:** scans dialogue + caption for every `claims_forbidden` term, "90+ countries", "inspected by hand", trailing periods in overlay text, etc. Violations → auto-regenerate with the violation named. The linter is code, so "no Halal in scripts" is *enforced*, not hoped for.
2. **Timing physics:** spoken dialogue at ~2.4–2.6 words/sec must fit its scene's `seconds` (with breathing room). Overflowing scenes are rejected back to the model with exact word budgets. Scripts that *actually fit* their runtime is precisely the polish generic tools skip.

**Revision loop:** operator feedback + current script → Opus returns the *full* revised script; bot shows a human diff ("Changed: scene 3 line, music mood; everything else untouched"). Every version stored; "go back to the first script" works.

---

## 7. Stage C — Storyboard keyframes (the cheap gate before the expensive one)

For each scene, composite a locked start frame with **nano-banana-2 (Gemini 3.1 Flash Image) via our existing kie.ai key — $0.04/image (1K)**. It natively takes multiple reference images (holds up to 5 characters / 14 objects consistent) — so the request is: character reference shots + the *real* pack-shot photo + setting prompt → John holding the actual orange/green-label carton. Label fidelity comes from referencing the real packaging photo, never from asking the model to invent packaging.

- **A/B at build time:** Seedream 5.0 (Lite $0.035 / Pro $0.075, up to 10–14 reference images, ByteDance's dense-text lineage) vs nano-banana-2 on our actual pack shots; pick per-use-case winner. Both are one env-var swap on kie.ai. (Arena image-edit, Aug 2026: Seedream 5.0 Pro #5, nano-banana-2 #10 — close; label legibility on *our* labels is the tiebreaker.)
- **Billing note:** the Aug 6 call preferred routing image gen through Higgsfield (client Amex) rather than our kie.ai key. Honor that where the API allows — the Higgsfield Cloud API hosts Seedream text-to-image, and Soul/keyframe work can run there — but kie.ai remains the verified multi-reference workhorse until the V0 spike proves the Higgsfield-side equivalent; at $0.04–0.09/frame the interim billing difference is noise.
- **Auto-QC pass (Claude vision) on every keyframe before the operator sees it:** right person? real packaging, label legible? no carcass / steam / spotlight? Composition sane? Fail → regenerate up to 2× with the failure named, then surface honestly.
- Keyframes are pennies (4–8 × $0.04–0.09). The storyboard approval happens *here*, before the $10–40 generation spend — the same "script preview first, saves money" principle from the Aug 6 call, extended to visuals.
- Approved keyframes are **locked**: the generation layer receives them as fixed start frames (the "lock source image + script" lesson, industrialized).

---

## 8. Stage D — Voice, then video

**Audio first, always.** For each speaking/VO line: ElevenLabs TTS with the character's locked `voice_id` via `POST /v1/text-to-speech/{voice_id}/with-timestamps` → audio + character-level timestamps. Audio-first means (a) scene video is generated/trimmed to match real audio duration — sync is guaranteed by construction, (b) caption timings are free if captions are requested, (c) a voice retake ("warmer, slower") costs pennies and *does not touch video*.

- ElevenLabs **Starter, $6/mo** — ~30 min/mo of audio, commercial license, Instant Voice Cloning + Voice Design. Our volume (~2–4 videos/wk ≈ 4–8 min of finished VO) fits with heavy retake headroom. Fallback vendor: Cartesia Pro ($5/mo, word-level timestamps) if voice-design results disappoint. (Play.ht is dead — acquired by Meta, shut down Dec 2025.)
- One additional locked **narrator voice** (not tied to a character) for Mode B voiceovers.

**Video generation — Higgsfield Cloud API** (verified 2026-08-09: an official developer API exists — `platform.higgsfield.ai`, async `POST /{model_id}` queue, `Authorization: Key {key}:{secret}`, official Python SDK `higgsfield-client`, **first-class webhooks** via `?hf_webhook=` with 2h retry, failed/NSFW generations auto-refunded). The client relationship stays where the Amex already is, and the same key also runs the third-party models the platform hosts (`bytedance/seedance/...`, `kling-video/...`, `bytedance/seedream/...`):

- `broll` scenes: real footage from `broll_assets` when tags match (free, more authentic), else locked keyframe → image-to-video via **`higgsfield-ai/dop/standard`** (documented: `image_url` + `prompt` + `duration`) or Seedance/Kling on the same API — subtle camera moves only, no particles/steam by prompt *and* by QC. Clips run ~5–15 s max per generation, which fits the per-scene design by construction (scenes are 4–10 s).
- `speaking` scenes: locked keyframe + our ElevenLabs audio → lip-synced clip. **Speak is not on the documented REST API today** — three routes, verified in this order as a V3 spike: (1) **Seedance 2.0 on the Higgsfield platform** (marketed with native audio + lip-sync — confirm audio input is exposed via API); (2) Higgsfield's **official MCP/CLI** (OAuth, draws normal plan credits, explicitly supports Speak + programmatic Soul-character training — driveable from our backend); (3) **Segmind's `higgsfield-speech2video` wrapper** (documented and priced: image + MP3 + prompt, 5/10/15 s, $0.86–$4.23/gen — takes our audio-first pipeline's MP3 as-is). Route (3) works today with zero unknowns, so the worst case is a known, priced fallback rather than a risk.
- **Identity lives in stored reference images, then in the locked keyframe — never in a prompt.** A character is generated once; from then on the compositor feeds the hosted reference URLs (front portrait first) plus the real pack shot to the image model, and the animator only moves pixels we already approved. Nothing at video time re-runs the description that created the face, so John looks like John in his fiftieth video exactly as in his first. This is why consistency doesn't depend on any provider feature. Higgsfield's **Soul ID** (persistent character trained from 20–80 photos, then usable across their image *and* video models) is an optional tightening later — trainable from our generated reference sheets via the MCP/CLI — but it's not exportable from their ecosystem, which is precisely why the roster's source of truth stays in Supabase (§3).
- Per-scene auto-QC (Claude vision on sampled frames): identity drift, packaging integrity, banned visuals → one auto-retry with adjusted prompt, then surface to operator with options.
- **Retention trap:** Higgsfield retains generated files for as little as 7 days — every artifact is downloaded into Supabase Storage the moment its webhook fires. (Our pipeline already assumes this; now it's mandatory.)
- **Provider abstraction:** `VideoGenProvider` interface — `talking_scene(keyframe, audio) → clip`, `broll_scene(keyframe, motion_prompt, seconds) → clip`. `HiggsfieldProvider` first; the drop-in fallback is **kie.ai running the same underlying models** (it hosts Seedance/Kling/Hailuo/Wan under our existing key — no Higgsfield-branded models anywhere on kie.ai/fal/replicate, but the models themselves are there), plus Segmind for Speak. No single point of failure.

**Cost-safety invariant:** every scene artifact is keyed by a content hash of (script version, keyframe id, audio id). A crash, retry, or resume **never regenerates a scene that already has an artifact** — generation money is spent at most once per scene version.

---

## 9. Stage E — Deterministic assembly (the free layer)

All brand and text work happens here, in the container we already ship (**ffmpeg + Chromium are in the production image today**; the VHS pipeline in `app/messaging/video.py` proved HTML-rendered overlay → ffmpeg compositing works on this exact stack).

Input is an **edit spec** — plain JSON, the only thing the vibe-edit LLM ever writes:

```json
{
  "version": 4, "aspect": "9:16",
  "timeline": [
    {"scene": 1, "clip": "s1_v2.mp4", "in": 0.0, "out": 4.8},
    {"scene": 2, "clip": "broll_packline_real.mp4", "in": 2.0, "out": 9.5}
  ],
  "voiceover": {"audio": "vo_v1.wav"},
  "music": {"track": "warm-confident-02.mp3", "gain_db": -17, "duck_db": -9},
  "captions": {"enabled": false, "style": "poppins-white-lower"},
  "end_slide": {"asset": "end_slide_official", "seconds": 3.0},
  "loudness_lufs": -14
}
```

Renderer = **ffmpeg driven by the spec, compiled by our code (never by the LLM):** `concat` hard cuts, VO track, music bed ducked under voice via `sidechaincompress`, optional captions as libass **ASS** subtitles (Poppins, word-timed straight from the TTS timestamps — karaoke-grade styling is native ASS), official end-slide, `loudnorm` to -14 LUFS, fade. Branded overlays (end-slide, any lower-thirds) are rendered from HTML/CSS by the existing Playwright renderer to transparent PNGs — pixel-identical brand control, same skills, zero new runtime. Same spec + same artifacts → stable output; re-render ≈ 1–2 min; **cost $0**. The JSON-spec→filtergraph compiler is a few hundred lines and every operation has precedent in `app/messaging/video.py`.

**Renderer alternatives — evaluated 2026-08-09, and why they lost:**
- **Remotion:** technically the most capable, but its license aggregates the *client's* headcount when the client owns/operates the project — Globex's size mandates the paid tier, and programmatic rendering specifically requires the Automators license at a **$100/mo minimum** (≈$6–12/video at our volume, vs $0). Ruled out on cost-shape, not capability.
- **HyperFrames (HeyGen, Apache-2.0, open-sourced 2026):** "write HTML, render video, built for agents" — deterministic headless-Chrome frame capture + ffmpeg encode, compositions are HTML/CSS with timing attributes, embeds MP4/audio natively. Same runtime footprint as our container and the best-aligned upgrade **if we later want animated branded motion graphics** (the approved video needs none — hard cuts + static end-slide). Young (~3 months public); adopt only when a concrete need appears, behind the same edit spec.
- **Hosted JSON→video APIs (Shotstack $39/mo, JSON2Video ~$50/mo, Creatomate):** competent, but they'd charge us monthly for a runtime we already ship, with constrained HTML fidelity and vendor lock-in. Pass.
- **Revideo:** MIT but lagging its commercial fork and not HTML/CSS-native. Pass.

The spec is the contract; the renderer stays swappable behind it.

**Master encode (one file satisfies IG + FB + LinkedIn, per their current API specs):** H.264 High profile + AAC 128 kbps 48 kHz stereo, MP4 with moov atom up front, 1080×1920 (9:16), fixed 30 fps, closed GOP 2–5 s, 4:2:0, VBR ≤25 Mbps. **Duration governor: Facebook only accepts API video as Reels, capped 3–90 s** — so the engine enforces ≤90 s hard (our 30 s target / 60 s cap already complies). LinkedIn company pages accept 9:16 natively (3 s–30 min, ≤500 MB) and IG Reels allows up to 15 min/300 MB — no per-platform crops are *required*; 1:1/16:9 variants stay an optional nicety behind the same spec. Second export: 720×1280 compressed preview for WhatsApp (≤16 MB — Twilio's docs currently contradict themselves at 16 vs 20 MB, so we engineer to the older, safer 16).

---

## 10. Stage F — The vibe-edit loop (why there's no endless loop)

Your instinct is right, and the pivot already proved it: *generatively* re-editing video is a fix-A-break-B treadmill. The fix is that **operator feedback never edits the video — it edits one of four artifacts, each cheaper and more deterministic than "regenerate":**

| Class | Examples | What changes | Cost / time |
|---|---|---|---|
| **1 · Post edit** | trim scene 2, reorder, cut scene, different music, add/remove captions, longer end-slide, "tighter" | edit spec JSON only → re-render | **$0 · ~1–2 min** |
| **2 · Audio edit** | "say it warmer", "slower", swap one word | re-TTS that line → re-render (video untouched for VO/b-roll; speaking scene needs re-lipsync only if mouth is on camera) | pennies–few $ · minutes |
| **3 · Scene regen** | "his hands look weird", "different angle on scene 3" | ONE scene: (optionally new keyframe) → regenerate that clip → splice via edit spec | ~$2–8 · minutes |
| **4 · Script change** | "make it about export instead", "add a scene about QC" | targeted script patch → only *affected* scenes flow through keyframe→gen again | scoped $$ |

Opus classifies each feedback message into a class **and says so with the price before spending**: *"That's a scene-3 regeneration (~$5) — go ahead?"* Class 1–2 just happen. Cost transparency on the client's own Amex is what keeps trust.

Every iteration is an immutable `video_versions` row (edit spec + artifact URLs + parent). "Go back to the version before" is a lookup, not a prayer. Fix A can never silently unfix B because B's artifact never changed.

---

## 11. Stage G — Approval and publishing

- Final "approve" → same approval-hold logic as static posts: future `publish_on` → held, auto-publishes 09:00 America/New_York on the date; today/none → publishes now.
- Default slot suggestion: next free **Tuesday or Thursday** ("That's your video day"), overridable to any day — matching "create any day, post any day".
- Blotato: **video posting confirmed** (verified 2026-08-09 against their live API docs) on the same `mediaUrls` path — requirements: **H.264 MP4 only** (no .mov); Facebook video must carry `mediaType: "reel"` plus the `pageId` our client already resolves; LinkedIn company pages need `accountId` + `pageId` (also already resolved); Instagram publishes video as a Reel with no extra fields. Media is URL-based — the Supabase Storage public URL works directly, with `POST /v2/media` re-hosting as the reliability option if platform fetches prove flaky. Per-platform in parallel, partial-failure reporting, `retry <platform>` command.
- The published row logs: script version, edit-spec version, all artifact URLs, spend breakdown, per-platform results — full provenance per video.

---

## 12. Data model (new)

- `characters`, `products`, `broll_assets` — §3.
- `videos` — id, brief, mode, character_id, product_id, state (mirrors conversation stages), current_script_version, current_video_version, publish_on, spend_cents_total, created_by.
- `video_script_versions` — video_id, version, script jsonb, source (initial/revision), feedback_text.
- `video_versions` — video_id, version, edit_spec jsonb, master_url, preview_url, parent_version.
- `video_scenes` — video_id, script_version, idx, kind, keyframe_url, clip_url, audio_url, content_hash (the never-pay-twice key), qc jsonb, spend_cents.
- `video_jobs` — the resumable pipeline ledger: video_id, stage, status, attempt, error, started/finished. A crash resumes from the last completed stage; artifacts make every stage idempotent.
- Storage buckets: existing `posts` bucket gains `videos/{video_id}/…` (keyframes, audio, scenes, masters, previews). `upload_video()` already exists.

Env additions: `ELEVENLABS_API_KEY`, `HIGGSFIELD_API_KEY` (or provider equivalent), `VIDEO_MONTHLY_BUDGET_USD`, `VIDEO_ENABLED` (master switch, same dormancy philosophy as the calendar).

---

## 13. Jobs & infrastructure

- **Same Railway service to start.** The pipeline is overwhelmingly IO-bound HTTP polling (TTS seconds, images seconds, video-gen minutes) plus ffmpeg for minutes at 2–4 videos/week — APScheduler + `asyncio.to_thread` subprocess (the VHS precedent) carries this fine. The seam is clean: if assembly ever contends with webhook latency, the same image runs as a second Railway service (`ROLE=video-worker`) reading the same `video_jobs` table. Don't pre-build the second service; do keep the job table designed for it (it is).
- Provider callbacks: webhook endpoint if the provider offers them, polling fallback either way (poller is required anyway for resume-after-crash).
- **Budget guard:** monthly spend tally vs `VIDEO_MONTHLY_BUDGET_USD`; warn in-chat at 80%, require an explicit "yes, over budget" confirmation past 100%. Never a surprise on the Amex.
- WhatsApp delivery (verified 2026-08-09): previews encoded H.264/AAC MP4 (the reliably deliverable set) at 720×1280, engineered under **16 MB** (Twilio pages disagree 16 vs 20 — take the safe one); Twilio validates the `content-type` header at the media URL, so Supabase must serve `video/mp4` correctly. If a cut still exceeds the cap → thumbnail + hosted link.
- **24-hour window & sandbox realities:** freeform messages (including video) are only deliverable within 24h of the operator's last inbound message — renders take minutes, so the window is nearly always open, but a "finished" ping after a long quiet gap needs an approved template on the production sender (**the sandbox enforces the window too and allows NO custom templates** — one more reason the production WhatsApp sender matters). Sandbox quirks that will bite demos if unknown: joins **expire after 72h** (rejoin required) and throughput is ~1 message per 3 s — storyboard delivery paces itself accordingly.

---

## 14. Voice & music (decided)

- **Voice: ElevenLabs — DONE 2026-08-09.** Account is **Pro** tier (commercial licence included, 160 voice slots, 10 used). All 10 personas designed via `POST /v1/text-to-voice/design` → saved via `POST /v1/text-to-voice`, ids locked on the roster. Speech runs through `/text-to-speech/{voice_id}/with-timestamps`, so caption timing arrives with the audio. **All voices are neutral American English.** ⚠️ The account's character quota is **91% consumed** for the current period (~60k left) — fine for our volume (a 30s VO is ~450 characters) but worth watching before a heavy retake day. Fallback if ever needed: Cartesia Pro.
- **Music: curated licensed pool, not a music API.** Programmatic music is either enterprise-gated (Epidemic/Artlist APIs are partnership-only) or generative-subscription (Mubert $199/mo — overkill at our volume). The fit: **Epidemic Sound Commercial (~$49/mo)** → hand-pick ~12–15 tracks matching the brand moods (warm/confident/industrial/uplift), drop into an asset pool tagged by mood (`music_pool/` — exactly the pattern `asset_pool/` already uses), scriptwriter picks by `music_mood` tag. Licensing clean for brand social including paid boosting; zero per-video cost; refresh the pool quarterly. Decision on who holds the subscription: §19.
- **Suno/AI music: no** — no official API (partner-gated as of Jul 2026) and murkier commercial-rights posture than a client brand should carry.

---

## 15. Cost model

Per typical video (~30s, 4 scenes, 2 speaking + 2 b-roll, one revision round):

| Item | Cost |
|---|---|
| Opus calls (script + revisions + classification + QC) | ~$0.3–0.8 |
| Keyframes (6–10 gens incl. retries @ $0.04–0.09) | ~$0.3–0.9 |
| TTS (~600 chars + retakes) | ~$0.1 |
| **Scene generation** | **~$3–15 typical — the dominant term** (b-roll clip ≈ $0.35–2.90 in Higgsfield credits at the ~$0.05/credit Plus rate, model-dependent; speaking clip via Segmind $0.86–4.23; premium models like Veo push the top end) |
| Assembly / re-renders / previews | $0 (Railway compute already paid) |
| **Marginal per video** | **≈ $5–20 typical, ~$40 worst case with heavy retries/premium models** — notably *under* the $10–40/edit observed in the manual design rounds, because the storyboard gate and never-pay-twice hashing eliminate the iteration waste that drove that number |

Fixed: ElevenLabs $6 + Epidemic ~$49 ≈ **$55/mo**. At full Tue/Thu cadence (~9 videos/mo): **≈ $145–400/mo generation spend** on the client's Amex — context: the system replaces a $45K/yr line item. Budget guard (§13) makes the ceiling explicit and chosen.

---

## 16. Edge cases & failure modes (the catalogue)

**Brief & libraries**
- Unknown character → closest-match offer or add-flow; never silently substitute.
- Unknown/ambiguous product ("chicken" → 6 SKUs) → ask once with the shortlist; remember the operator's habitual pick as an alias.
- Real person requested (e.g. "video of Alan") → hard block without `likeness_consent`; explain kindly, offer a persona alternative or the consent path.
- Retired character requested → say so, offer roster.
- Product with no pack shot → request a photo, or proceed with studio-style generic *with an explicit warning* (violates the real-packaging rule → recommend against).

**Content safety & brand**
- Brief demands graphic raw product ("show the carcasses") → the visual_rules flag triggers: warn (Len's own rule), offer packaged/plated framing. The system politely protects the client from itself.
- Brief demands a forbidden claim ("say we're Halal certified") → linter blocks; bot explains it's on the client's own no-say list and offers compliant phrasing.
- Off-brand asks (competitor mentions, political tie-ins, meme-jacking) → decline per brand rules, offer the nearest on-brand angle.
- Every script/regen passes the linter — including *revisions* (a revision can't sneak a banned phrase back in).

**Generation**
- Scene fails 3× (provider error / QC reject) → operator gets the scene, the reason, and 3 options: retry / tweak that scene's script / convert to b-roll+VO (Mode B degrade).
- Identity drift (John stops looking like John mid-scene) → frame-sampled QC catches; regen with tightened reference weighting.
- Label gibberish on packaging → swap compositor (nano-banana-2 ↔ Seedream) for that keyframe; worst case, real pack shot is composited in post over the held product region (the static system's exact philosophy: brand pixels never left to the model).
- Lipsync desync → audio is the source of truth; regenerate the clip against the same locked audio, never trim audio to fit video.
- Provider outage / credit exhaustion / Amex decline → job pauses cleanly at a stage boundary, operator informed with resume command; nothing half-spent (never-pay-twice hashing).

**Conversation & ops**
- Double "approve" taps → idempotent per version id; second tap is a friendly no-op.
- Edit request lands while rendering → queued, acknowledged ("finishing the current cut, then applying that").
- New video request mid-generation → queued as next video, told transparently.
- Post + video both pending → qualified approvals (§4.3).
- "cancel" mid-generation → stops before the next paid stage; sunk spend reported honestly.
- Voice-note brief → Whisper transcription — **dependency: `OPENAI_API_KEY` is still unset in prod** (already promised for the static system; video inherits the fix).
- Operator sends their own video file → ingested into `broll_assets` (tag prompt follows) — real footage is a gift, treat it as one.
- Two operators (Ilan + Karen) → per-phone conversation state already isolates flows; approvals only from `AUTHORIZED_NUMBERS`; a video's requester gets its notifications.

**Delivery & publishing**
- Preview over the WhatsApp cap → compress harder → link+thumbnail fallback.
- "Finished" ping outside the 24h service window (long render, quiet operator) → template-message path on the production sender (sandbox is lenient; production must be correct).
- Blotato partial failure (IG ok, LinkedIn fails) → per-platform report + `retry linkedin`.
- Approved-and-held video, then "wait, change it" before publish day → un-hold → back to VIDEO_REVIEW → re-approval required (the publish job only ever publishes the *approved* version id).
- Platform AI-disclosure rules → §17.

---

## 17. Ownership, compliance, accounts

| Account | Today | Needs to be |
|---|---|---|
| Higgsfield workspace | **Sonder's login locally; client Amex loaded (whose workspace?)** | client-owned **before** V3 — a video library in a third party's workspace is the Twilio-ownership problem again, but with paid assets. Higgsfield sells exactly the right shape: **Business plan ($89/seat/mo, 2–15 seats, shared credit pool) / Team workspaces** with shared asset library and admin roles — a Globex-owned workspace with Abdul + Ilan seats, plus a Cloud API key from its dashboard. V0 task, decided with Abdul/Ilan |
| ElevenLabs | none | recommend client-owned like Railway; voices are brand assets |
| kie.ai | ours (existing key) | fine short-term; revisit at handover |
| Epidemic Sound | none | subscription holder = whoever owns publishing risk — recommend client |

- **Likeness:** invented personas only, by default; real people require recorded consent (§3.1). The character sheets get one-time client approval — which also means Len has personally approved every face that will ever front the brand.
- **AI-content disclosure (verified 2026-08-09):** Meta **requires** disclosure when organic content contains photorealistic AI-generated video, may penalize failures, and auto-labels via industry-standard C2PA/IPTC metadata detection. LinkedIn auto-labels files carrying C2PA credentials and only *mandates* disclosure for impersonation-style synthetic media. Two practical consequences: (1) **assembly must not strip C2PA metadata** — ffmpeg re-encodes drop metadata by default, so the pipeline explicitly preserves/re-attaches content credentials on the master; (2) Blotato exposes an `isAiGenerated` field only for TikTok — there is no Meta/LinkedIn disclosure field in its publish API — so Meta-side compliance rides on the metadata path (plus, if needed, a one-time manual toggle habit in-platform). Logged as a compliance item to re-check at V5, not an afterthought.
- **Claims:** the linter (§6) is the compliance floor (no Halal, etc.); `claims_forbidden` is per-product extensible the day a market-specific rule appears.

---

## 18. Build phases

**V0 — Foundations** *(no user-visible change)* — **partially shipped 2026-08-09**
- [x] Higgsfield workspace decision → **use the existing account** (see §19)
- [x] `characters` / `products` / `broll_assets` (+ `videos`, `video_scenes`, `video_versions`, `video_script_versions`, `video_jobs`) tables in `app/db/schema.sql`
- [x] Character roster written: **10 personas, 5M/5F, Asian+African+Caucasian in both genders, plus Latino and South Asian, ages 24–58** — `app/data/characters.json`
- [x] Product library skeleton from the pack shots we hold — `app/data/products.json` (10 entries, poultry + duck)
- [x] `app/video/library.py` — resolution, presenter matching, forbidden-claims linter (22 tests)
- [x] `scripts/generate_characters.py` — data-driven character-sheet generator (prompts verified; blocked on an image key)
- [x] Image key supplied → **all 10 character sheets generated on GPT Image-2** (`gpt-image-2-text-to-image` via kie.ai, 1K) + branded contact sheet at `app/data/characters/_contact_sheet.png`
- [x] **All 10 voices designed and locked** (ElevenLabs Pro — commercial licence included, 10/160 slots used); `voice_id` recorded per character; 3 audition takes kept per persona so a different take can be chosen without paying to regenerate
- [x] Speech verified end-to-end: locked voice → audio + **word-level caption timings** in one call (`voices.speak`)
- [x] **Len approved the full roster 2026-08-09** — all 10 flipped to `status: "approved"` with `approved_at` recorded, plus an `_meta.approval` audit block (approver, date, scope). **10/10 usable.** A new or replacement persona still re-enters as `draft` and needs its own approval — approving the seed roster does not approve future ones (test-enforced)
- [ ] Epidemic subscription + first 12-track music pool
- [ ] **API spike (half a day, de-risks everything):** confirm on the live Cloud API — 9:16 on video models, the Speak route (Seedance-2.0-audio vs MCP/CLI vs Segmind), end-frame param, API tier gating, real per-clip prices off the generate confirmations
- [ ] Send Ilan the SKU-list ask + the three outstanding pack shots (§19.9)
- Acceptance: "list characters" / "list products" answer correctly in WhatsApp (read-only slice shippable early)

**V1 — Script engine** *(value with zero media spend)*
- [ ] `new_video_request` intent + `VIDEO_INTAKE` + brief parsing
- [ ] `VideoScript` generation + linter + timing validator + revision loop + versioning
- Acceptance: full script conversation E2E on the sandbox; linter provably blocks a planted "Halal"

**V2 — Storyboard**
- [ ] Voice creation per character (locked ids); TTS with timestamps
- [ ] Keyframe compositing (nano-banana-2 vs Seedream A/B on real pack shots) + auto-QC + storyboard preview
- Acceptance: "John + duck carton + shipping" storyboard where the label is legible and John matches his sheet

**V3 — Generation & assembly**
- [ ] `VideoGenProvider` + Higgsfield integration (per-scene, resumable, never-pay-twice)
- [ ] Edit-spec renderer (ffmpeg + HTML overlays): cuts, VO, ducked music, end-slide, LUFS
- [ ] Preview delivery (compressed) + stage pings + `status`
- Acceptance: one full video E2E on sandbox, spend logged, ≤ target cost

**V4 — Vibe-edit loop**
- [ ] Feedback classifier (4 classes) + cost-confirm gates + `video_versions` + rollback
- Acceptance: canonical edits ("different music" / "warmer read" / "fix scene 2" / "make it about export") each route to the right class; class-1 turnaround ≤ 2 min

**V5 — Publish**
- [ ] Blotato video publish + per-platform variants + partial-failure retry + Tue/Thu default + approval-hold reuse + provenance logging
- Acceptance: dummy-account publish of a held video on its date

**V6 — Exceed**
- [ ] Tue/Thu morning nudge with 2 concrete video ideas (drawn from the live calendar: upcoming trade shows/holidays — the two systems feed each other)
- [ ] Real-footage ingest flow polish; monthly spend recap message; (later) multilingual variants for market-specific posts

---

## 19. Decisions and open questions

**Decided 2026-08-09 (Abdul):**
1. ~~Higgsfield workspace~~ → **use the existing account as-is.** No migration now. The ownership note in §17 stands as a flag to revisit at handover, not a blocker: keep the roster, voices, pack shots and every rendered artifact mirrored in client-owned Supabase so a later workspace move costs nothing but re-uploading references.
2. ~~Character roster~~ → **generate 10 fresh personas**, not inherited imagery. Diversity is specified, not incidental: **Asian, African and Caucasian in both genders**, plus Latino and South Asian, with ages spread across the range. Roster is written and test-enforced (§3.1).
3. **Products** → **skeleton now from the pack shots we actually hold** (poultry + duck, 10 entries); pork, beef, seafood and grains land later when Ilan supplies the SKU list and photography. Adding a product is a JSON entry, no code change.

**Still open:**
4. **Real b-roll:** can Ilan source real factory/port clips? (Biggest single quality lever; the approved video was real footage.)
4. **Music subscription holder** (client vs us) — and any taste guidance for the first 12-track pool?
5. **LinkedIn:** include for video, or IG/FB first?
6. **Monthly generation budget** for the guard rail — propose $400/mo to start?
7. Confirm Tue/Thu as the *suggested* default slots (creation any day, posting any day stays true).
8. **`KIE_API_KEY` is empty in `.env`** — character sheets cannot be generated until an image key is supplied (or the Higgsfield route is wired in V3). This is the one thing blocking the roster from becoming pictures Len can approve.
9. **Dedicated pack shots** for whole bird, gizzards and frames. We hold only raw shots of these, and the no-carcass rule bars raw imagery from leading a post, so they currently fall back to the generic branded carton (flagged in `products.json` and `docs/missing_assets.md`).

---

## 20. Verification notes

All tool facts in this plan were verified against the live web on **2026-08-09** across four research passes: (1) Higgsfield — official Cloud API confirmed (`platform.higgsfield.ai`, key:secret auth, webhooks, Python SDK, DoP/Seedance/Kling model ids, Soul ID, Business/Team workspaces; no Higgsfield models on kie.ai/fal/replicate; Segmind wrappers priced); (2) edit layer — Remotion's aggregated-headcount licensing + $100/mo Automators minimum, HyperFrames (Apache-2.0), ffmpeg libass/sidechaincompress substrate; (3) publishing/delivery — Blotato video support + per-platform requirements, FB Reels 3–90 s governor, LinkedIn 9:16 org video, Twilio 16-vs-20 MB contradiction, sandbox 72 h join expiry + 24 h-window enforcement, Meta mandatory AI-disclosure + C2PA auto-labeling; (4) voice/music/compositing — ElevenLabs Starter + timestamps endpoint, music API gating (Epidemic/Artlist partner-only; Mubert/Soundstripe self-serve), nano-banana-2 and Seedream 5.0 multi-reference + kie.ai pricing, arena rankings.

**Known UNVERIFIED items, deliberately parked as the V0 spike:** Speak on the official REST API (vs MCP/CLI vs Segmind), Soul ID training via REST, the 9:16 enum on the REST video endpoints, end-frame parameter via REST, whether Cloud API access is gated to higher plan tiers, and the exact per-model API price sheet (Higgsfield shows prices on the generate confirmation rather than publishing a table). None of these changes the architecture — each has a verified fallback named inline above.

## What actually got built (module map)

| Module | Does |
|---|---|
| `app/video/models.py` | `VideoScript` / `Scene` / `EditSpec` / `EditDecision` — the screenplay and the cut, kept separate on purpose |
| `app/video/script.py` | Brief parsing, screenplay generation, **claims linter + timing physics**, revision loop, human diff |
| `app/video/keyframes.py` | Character reference + real pack shot → locked 9:16 start frame per scene |
| `app/video/voices.py` | Locked per-character voices; speech with word timings |
| `app/video/providers.py` | `VideoGenProvider` → Kling AI Avatar (speaking, audio-driven) / Kling i2v (b-roll) on kie.ai |
| `app/video/assembly.py` | Edit spec + clips → master MP4: cuts, VO, ducked music, end slide, −14 LUFS, platform-safe encode |
| `app/video/captions.py` | Word-timed ASS captions from the TTS alignment (off by default) |
| `app/db/videos.py` | Video state on the existing `posts` table — inherits the approval lifecycle, needs no new DDL |
| `app/workflows/video.py` | Orchestration, both approval gates, the 4-class vibe-edit loop, publishing |
| `scripts/make_video.py` | Runs the whole pipeline from a terminal, printing what WhatsApp would send |

**WhatsApp wiring:** `IntentType.new_video_request` + `ConversationState.VIDEO` + `Action.GENERATE_VIDEO`. A live video owns the conversation, so approve / cancel / feedback all reach it before the post router sees them.

## Progress log

- **2026-08-09 (pipeline built + proven)** — **The whole engine works end to end.** One sentence in (*"make a video of John holding our duck retail pack and talking about how we ship it"*), a **30.2s 1080×1920 H.264+AAC video** out: 4 scenes, John lip-synced to his own locked voice, official end slide. Deployed to production (commits b56f537, 3d6590e). **No Higgsfield credentials were needed** — kie.ai hosts `kling/ai-avatar-standard` (speaking, driven by our ElevenLabs audio) and `kling/v3-turbo-image-to-video` (b-roll) under the key we already had; Higgsfield still drops in behind `VideoGenProvider` whenever we want it. Cost: **~$18 of credits for one 4-scene video**; script-only runs are free. **Four real bugs the live run exposed and the tests never would have:** (1) product pack shots were never hosted, so the compositor got dead URLs and the image model returned an opaque 500; (2) `-shortest` with a voiceover shorter than the picture truncated the video and **cut the end slide off every video** — fixed with `apad`, now test-covered; (3) scene-cache uploads sat between the operator and their finished video for 40 minutes on a slow uplink — caching now happens after delivery; (4) the storage client's default timeout is sized for images, not 18MB clips. Suite **259 passed / 47 skipped**, ruff + mypy clean.

- **2026-08-09 (approved)** — **Len approved the full roster.** All 10 personas flipped to `status: "approved"` with `approved_at` per character and an `_meta.approval` block naming the approver, date and scope. The flip script refused to approve any character lacking reference images or a locked voice, so an approved character is always complete. **V0 is now done: 10/10 characters usable, each with 4 hosted reference shots and a locked American-English voice.** The approval gate itself survives — a new persona re-enters as `draft` and needs its own sign-off, which is test-enforced so approving this roster can never implicitly approve a future one. Suite **229 passed / 47 skipped**. Next: V1 (script engine), which needs no further credentials.
- **2026-08-09 (voices)** — **All 10 voices designed and locked, all neutral American English** per Abdul: Globex is a US company, accent variation was rejected in the design rounds, and an accent must never be inferred from a persona's appearance. Every `voice_direction` rewritten to distinguish characters by age, register, pace and warmth instead; the rule lives in `_meta.voice_rule` and is enforced by tests that fail on any "inflected"/regional-accent wording. New `app/video/voices.py` (design → save → speak-with-timestamps, plus character→word alignment grouping) and `scripts/create_voices.py` (idempotent, keeps 3 audition takes per persona, and lints its own audition line against the no-say list). ElevenLabs is **Pro** — commercial licence included. Suite **228 passed / 47 skipped**.
- **2026-08-09 (final)** — **Reference images wired as the identity source.** All 40 shots (4 per character) generated, uploaded public-read to Supabase Storage, and their URLs recorded on each record front-portrait-first via `scripts/upload_character_refs.py`. `Character` gained `reference_paths` / `primary_reference` / `has_references`, and **`usable` now requires stored references** — so a character can never be used in a video by re-running its prompt, only by referencing its approved face. Suite **222 passed / 47 skipped**. (The 10 shots that failed the first full run were connection exhaustion from a wide fan-out, not content rejections; concurrency lowered 4→3 and all succeeded on retry.)
- **2026-08-09 (later still)** — **Character sheets generated and rendered for approval.** kie.ai key supplied; image generation for the video engine runs on **GPT Image-2** (`gpt-image-2-text-to-image`) rather than the post pipeline's nano-banana — identity fidelity here is inherited by every later keyframe. `image_gen.generate()` is now model-aware (GPT Image takes `resolution`, rejects `output_format`) with a `model`/`resolution` override, covered by 3 new tests. All 10 personas rendered at 1K; `scripts/build_character_sheet.py` composes them into a branded contact sheet via the project's own Playwright renderer (`app/templates/html/_character_sheet.html`). **Spend: 632 credits for 22 images (~29 each), 5,763 left.** Two operational findings: (1) GPT Image's safety filter rejects food-plant shots with exposed raw product, so the context framing now requires sealed/packaged product — which is the client's own rule anyway; (2) it also false-positives on a numeric young age for a young woman, so Priya's prompt describes her as "in her mid-twenties, an adult professional" (noted inline in her record). Suite **218 passed / 47 skipped**, ruff + mypy clean.
- **2026-08-09 (later)** — **V0 skeleton built.** Decisions locked (§19): existing Higgsfield account, 10 fresh characters with an explicit diversity spec, product library seeded only from pack shots we hold. Shipped: `app/data/characters.json` (10 personas), `app/data/products.json` (10 products), the eight video-engine tables appended to `app/db/schema.sql`, `app/video/library.py` (handle-then-token resolution, presenter matching, forbidden-claims linter), `scripts/generate_characters.py`, and `tests/test_video_library.py` — **22 tests, suite 215 passed / 47 skipped, ruff + mypy clean (74 files)**. Tests encode the client's rules as executable constraints: the diversity spec, "a draft character can never front a video", "a real person needs recorded consent", "'raw chicken' is ambiguous so ask rather than guess", and "no raw carcass shot may lead a post". That last one caught a real gap — whole bird, gizzards and frames have only raw photography, so they fall back to the branded carton until dedicated pack shots exist. **Blocked:** `KIE_API_KEY` is empty, so the sheets can't be rendered into images yet.
- **2026-08-09** — Plan authored (nothing built). Sources: approved `ugc edited .mp4` + design-bible feedback rounds + Aug 6 call decisions (10 characters, asset bank, script-preview-first, vibe-edit via WhatsApp) + live tool research. Repo head start confirmed: ffmpeg+Chromium already in the production image, `storage.upload_video()` exists, VHS ffmpeg-overlay pipeline proven, Blotato publishes by media URL, Tue/Thu already reserved in `calendar_source`.
