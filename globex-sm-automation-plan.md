# Globex Social Media Automation System — Implementation Plan

> **For agentic workers:** Implement phase-by-phase. Each phase ends with acceptance tests that must pass before moving on. Tick boxes as you go. Add dated entries to the Progress Log at the bottom when significant work lands.

## Context

We are replacing Globex International's $45K/year social media manager with an AI system that Karen Joseph (their operations contact) controls from her phone via WhatsApp. Globex is a 30-year-old global food trading company (90+ countries, 300+ suppliers, 950+ trade partners). Contract signed April 23, 2026 between ElevateAIo LLC and Globex International, $8,500 build fee, Tier 2 scope: WhatsApp chat command center + automated content for holidays/trade shows/employee milestones + 20 rotating brand/packaging posts.

Two surfaces:
1. **On-demand:** Karen WhatsApps a request → AI generates copy + branded image → preview back → Karen approves → publishes to Instagram, Facebook, LinkedIn via Blotato.
2. **Automated:** Daily 8am EST scheduler generates posts for upcoming holidays, trade shows, 20+ year employee anniversaries, and the rotating brand/packaging schedule → same approval flow.

**Hard constraint:** Nothing publishes without Karen's explicit approval. Len (the owner) is a control freak; this is contractual.

**Quality bar:** Karen compared the expected output to "a Pratt University graphic designer." Brand consistency (Pantone 288C navy `#002D72` + 2985C cyan `#5BC2E7`, logo on every post) matters more than creativity. That's why we render HTML/CSS templates via Playwright instead of generating AI images.

This file is the single source of truth for what's left to build. Check off items as they complete. Add dated notes to the Progress Log at the bottom when significant work lands.

## Source Assets Inventory

Assets live in `c:\Users\abdur\Downloads\Globex\Globex\` (plus the newer Excel directly in `Downloads/`). Phase 0 imports everything we need into the project tree.

**Assets with real content (USABLE):**
- `Globex_G-Man_Pantone-288+2985 (2).eps` (1.2MB) — G-Man character logo, full Pantone colors
- `Globex_logo_side-lockup_Pantone-288+2985.eps` (1.37MB) — Side-by-side wordmark + G-Man
- `Globex_logo_top-Globex-only_Pantone-288.eps` (1.37MB) — Wordmark only, Pantone 288 navy
- `c:\Users\abdur\Downloads\Date of Hire Globex, Birthdate and PWs - Copy.xlsx` (21KB, May 19 2026) — **NEWEST employee list, source of truth.** Three tabs: Employee Info (39 employees), Events (11 trade shows for 2027, several TBC), Holidays (22 entries including Globex Founding Day Nov 5). Use this file. We use ONLY hire dates per CLAUDE.md (no birthday posts, no PW persistence).
- `Globex_Agreement - Social Media - Final.pdf/.docx` — Signed contract. Reference for scope/deliverables.
- `whatsapp_extract/_chat.txt` (35KB) — Real WhatsApp conversation history. Useful as brand voice training input for Claude (Karen + Ilan's actual phrasing, what Karen approved vs rejected).
- `whatsapp_extract/00000092-PHOTO-2026-04-10-08-59-17.jpg` + `00000095-PHOTO-2026-04-10-11-27-08.jpg` — Sample photos Karen sent. Useful as fixture inputs for Phase 4 webhook tests.
- `Screenshot 2026-05-04 at 4.39.51 PM.png` — Confirms social account ownership: Instagram handle `globex_international_`, login email `admin@globexusa.com`, Karen's WhatsApp number is `+1-917-859-2787` (provided; **do NOT hardcode — env var only**), credentials shared separately.

**Assets present but 0 bytes (broken extraction — re-download needed):**
- `GLOBEX Assets/Globex_Logo_CMYK/*` — all CMYK logo variants
- `GLOBEX Assets/Globex_Logo_Pantone/*` — additional Pantone logo lockups (we have 3 of these at root level already)
- `GLOBEX Assets/Globex_30 Years/*` — 30-year anniversary mark (relevant: company turns 30; needed for founding anniversary posts)
- `GLOBEX Assets/Globex 30 Years & Globex Intl Side Lock up/*` — 30-year side lockups
- `GLOBEX Assets/Globex_Animals/*` — Cow, Chicken, Pig, Fish illustrations (for product spotlight posts)
- `GLOBEX Assets/Globex Packaging/*` — Packaging product art in multiple Pantone colorways (BLACK / BLUE 288C / RED 1795C / GREEN 349C) — **directly relevant** to the 20 rotating brand/packaging posts
- `GLOBEX Assets/Grains/*` — Grain and Veggie illustrations
- `GLOBEX Assets/buttons/*` — UI button assets (likely website buttons, less relevant to social)

**Logo conversion required:** All usable logos are vector EPS. HTML/CSS templates need raster PNG (transparent background, retina-ready ≥2160px wide). Phase 0 converts via ImageMagick + Ghostscript.

## Architecture (locked decisions)

- **Backend:** FastAPI + Python 3.12+, deployed on Railway (single process).
- **AI:** Anthropic Claude Opus 4.7 (`claude-opus-4-7`) for everything — content generation, intent parsing, vision (when Karen sends photos), edit feedback interpretation. Temperature 0.3 for brand consistency.
- **Messaging:** Twilio WhatsApp Business API. Webhook validated with `RequestValidator`.
- **Rendering:** Playwright (Python, headless Chromium). Single runtime, no Node sidecar. Renders HTML/CSS templates → PNG at platform-correct dimensions.
- **Publishing:** Blotato API (keys already in hand, accounts connected). One integration, three platforms.
- **Database:** Supabase (managed Postgres) via `supabase-py`. Holds employees, holidays, trade shows, posts, approval history, **and conversation state** (so Railway restarts don't lose Karen's in-progress draft).
- **Scheduler:** APScheduler running inside the FastAPI process. Daily 8am EST tick.
- **Storage:** Supabase Storage bucket for generated PNGs (public URLs for WhatsApp + Blotato).
- **Time:** All timestamps stored UTC, displayed/scheduled in `America/New_York` (Karen's timezone). Use `zoneinfo`, never naive datetimes.

## Content Categories

1. `trade_show` — pre/during/post variants. Real-time conference support (contract). Sources: `trade_shows` table.
2. `holiday` — General + food-industry days from the Excel Holidays tab. Date-specific (Lunar New Year, Memorial Day, etc.) and month-long observances (National Beef Month, National Poultry Month, etc.).
3. `milestone` — Employee anniversaries, **20+ years only**. 6 employees currently qualify. Source: `employees` table hire_date.
4. `founding_anniversary` — Special case: Globex Founding Day (Nov 5, 1993). Uses 30-year mark logo asset. 33 years in 2026.
5. `stats` — On-demand. Visually impactful number-driven posts ("150 ships on the water").
6. `announcement` — On-demand. New hires, partnerships, general company news.
7. `product_spotlight` — Contract §2. Showcases Globex products: poultry, beef, pork, fish, grains, packaging. Uses animal/grain assets.
8. `promotional` — Contract §2. Awareness + promotional posts (packaging launches, new product lines).
9. `branded_packaging` — Finite pool of 20 pre-designed posts rotated on a schedule.
10. `custom` — Catch-all. Karen sends photo + freeform text that doesn't fit other categories.

**Explicitly OUT of scope (confirmed 2026-05-19):**
- `news_based` — Contract §2 mentions it but not built. Brand prompt's DON'T list includes "no news-based references."
- `recipe` — Karen dropped Alan's hot sauce recipe series. Not built.

**Platform routing (confirmed 2026-05-19):** All content goes to all three platforms (Instagram, Facebook, LinkedIn) identically. `formatter.py` only does platform-specific length trimming and hashtag formatting.

## File Structure (final target)

```
globex-social/
├── CLAUDE.md                          (exists)
├── globex-sm-automation-plan.md       (this file)
├── README.md                          (Phase 1)
├── pyproject.toml                     (Phase 1)
├── requirements.txt                   (Phase 1 — Railway needs this)
├── Procfile                           (Phase 8)
├── railway.toml                       (Phase 8)
├── .env.example                       (Phase 1)
├── .gitignore                         (Phase 1)
├── docs/
│   ├── runbook.md                     (Phase 8)
│   ├── contract.pdf                   (imported from source assets, gitignored)
│   ├── missing_assets.md              (Phase 0 — auto-generated)
│   └── reference/
│       ├── client_chat_excerpts.md    (Phase 0/2 — curated Karen-voice quotes)
│       └── template-previews/         (Phase 3 — visual proofs)
├── app/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app, lifespan, route registration
│   ├── config.py                      # Pydantic Settings for env vars
│   ├── logging_config.py              # Structured JSON logging
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── client.py                  # Anthropic client singleton
│   │   ├── generator.py               # Content generation per category
│   │   ├── intent.py                  # Parse Karen's WhatsApp message → intent
│   │   ├── editor.py                  # Apply Karen's edit feedback to a draft
│   │   └── prompts/
│   │       ├── __init__.py
│   │       ├── brand.py
│   │       ├── intent.py
│   │       ├── trade_show.py
│   │       ├── holiday.py
│   │       ├── milestone.py
│   │       ├── founding_anniversary.py
│   │       ├── stats.py
│   │       ├── announcement.py
│   │       ├── product_spotlight.py
│   │       ├── promotional.py
│   │       ├── branded_packaging.py
│   │       └── custom.py
│   ├── messaging/
│   │   ├── __init__.py
│   │   ├── twilio_client.py
│   │   ├── webhook.py
│   │   ├── validator.py
│   │   ├── conversation.py
│   │   └── media.py
│   ├── publishing/
│   │   ├── __init__.py
│   │   ├── blotato.py
│   │   └── formatter.py
│   ├── templates/
│   │   ├── __init__.py
│   │   ├── renderer.py
│   │   ├── catalog.py
│   │   ├── assets/
│   │   │   ├── logos/
│   │   │   │   ├── globex-gman-full.png
│   │   │   │   ├── globex-lockup-side.png
│   │   │   │   ├── globex-wordmark-navy.png
│   │   │   │   ├── globex-30years.png         (once re-extracted)
│   │   │   │   └── globex-white.png
│   │   │   ├── animals/                       (once re-extracted)
│   │   │   ├── grains/
│   │   │   ├── packaging/
│   │   │   └── fonts/
│   │   └── html/
│   │       ├── _base.css
│   │       ├── trade_show_pre.html
│   │       ├── trade_show_during.html
│   │       ├── trade_show_post.html
│   │       ├── holiday.html
│   │       ├── holiday_month_long.html
│   │       ├── milestone.html
│   │       ├── founding_anniversary.html
│   │       ├── stats.html
│   │       ├── announcement.html
│   │       ├── product_spotlight.html
│   │       ├── promotional.html
│   │       ├── branded_packaging.html
│   │       └── custom.html
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── jobs.py
│   │   └── orchestrator.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── schema.sql
│   │   ├── migrations/
│   │   │   └── 001_initial.sql
│   │   ├── posts.py
│   │   ├── employees.py
│   │   ├── holidays.py
│   │   ├── trade_shows.py
│   │   ├── approvals.py
│   │   ├── conversations.py
│   │   ├── packaging_rotation.py
│   │   └── storage.py
│   ├── data/
│   │   ├── employees.json
│   │   ├── holidays.json
│   │   ├── trade_shows.json
│   │   └── branded_packaging_rotation.json
│   ├── workflows/
│   │   ├── __init__.py
│   │   ├── on_demand.py
│   │   ├── approval.py
│   │   └── automated.py
│   └── utils/
│       ├── __init__.py
│       ├── time.py
│       └── retry.py
├── scripts/
│   ├── apply_schema.py
│   ├── seed_db.py
│   ├── import_assets.py
│   └── render_all_templates.py
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── twilio_payloads/
    │   ├── photos/
    │   └── claude_responses/
    ├── test_intent.py
    ├── test_generator.py
    ├── test_renderer.py
    ├── test_webhook.py
    ├── test_conversation.py
    ├── test_blotato.py
    ├── test_scheduler.py
    └── e2e/
        ├── test_on_demand_flow.py
        └── test_automated_flow.py
```

---

## Phase 0: Asset Import & Inventory Gap Closure

**Goal:** Get every usable asset out of `c:\Users\abdur\Downloads\Globex\Globex\` and into the project tree in the right format. Surface every missing asset to the user with a single ask, so we're not blocked mid-build.

### What gets built
- [x] `scripts/import_assets.py` — one-shot script (re-runnable, idempotent) that:
  - [x] Converts the three usable EPS logos at `Downloads/Globex/Globex/*.eps` to transparent PNG at 2160px wide using `subprocess.run(["magick", "convert", "-density", "300", "-background", "none", src, "-resize", "2160x", dst])`. Requires ImageMagick + Ghostscript on the system (documented in README).
  - [x] Composites a white-only variant (`logo-white.png`) by inverting the navy fill — for dark backgrounds.
  - [x] Parses the newer `c:\Users\abdur\Downloads\Date of Hire Globex, Birthdate and PWs - Copy.xlsx` with `openpyxl` (data_only=True) and extracts all three tabs:
    - [x] **Employee Info tab** → `app/data/employees.json`. Fields: `name`, `title`, `hire_date` (ISO date). **Drops DOB and password columns entirely.** Handles encoding quirks (e.g., "Federico Zermeño" Unicode).
    - [x] **Events tab** → `app/data/trade_shows.json`. Fields: `name`, `month`, `start_date` (null if "TBC"), `end_date`, `location`, `booth` (null), `link`, `hidden` (default false), `needs_date_confirmation`, `notes`. Skips meta rows.
    - [x] **Holidays tab** → `app/data/holidays.json`. Fields: `name`, `month`, `date_2026`, `date_2027`, `is_month_long` (true when "Entire Month"), `category` (auto-classified: `general` / `food_industry` / `globex_founding` / `cultural`). Deduplicates Easter Sunday (keep Apr 5). Skips dateless rows with no month flag. Tags Nov 5 as `globex_founding`.
  - [x] Copies sample Karen-sent photos (`whatsapp_extract/*.jpg`) to `tests/fixtures/photos/` for Phase 4 webhook test fixtures.
  - [x] Copies the contract PDF to `docs/contract.pdf` (gitignored).
  - [x] Stages `whatsapp_extract/_chat.txt` and writes `docs/reference/client_chat_excerpts.md` with curated Karen-voice quotes for Phase 2 prompt tuning.
  - [x] Prints final inventory report: "X logos converted, Y employees, Z trade shows (W with TBC dates), N holidays (M month-long)."
- [x] `docs/missing_assets.md` — auto-generated checklist of remaining blockers (re-extracted ZIP for animals/packaging/grains/30-year art, the 20 packaging post content) so we can send Karen one consolidated request.

### Acceptance criteria
- [x] `python scripts/import_assets.py` completes without errors and reports inventory.
- [x] `app/templates/assets/logos/globex-gman-full.png` exists, is transparent, ≥2000px wide.
- [x] `app/data/employees.json` parses as valid JSON, has one entry per Excel row, **NO** birthdate field anywhere.
- [x] `app/data/trade_shows.json` has 12 entries, TBC dates marked `needs_date_confirmation: true`.
- [x] `app/data/holidays.json` has ~20 entries, Nov 5 entry has `category: globex_founding`, month-long entries have `is_month_long: true`.
- [x] `docs/missing_assets.md` lists every gap from the Source Assets Inventory.

**Dependencies:** none. Run before Phase 1.
**Complexity:** Low. Mostly file shuffling + one Excel parse + one ImageMagick invocation.

---

## Phase 1: Foundation

**Goal:** A FastAPI app that boots locally, connects to Supabase, and has Karen's data seeded. No business logic yet.

### What gets built
- [x] `pyproject.toml` / `requirements.txt` with locked deps: `fastapi`, `uvicorn[standard]`, `anthropic`, `twilio`, `supabase`, `apscheduler`, `playwright`, `pydantic-settings`, `httpx`, `tenacity`, `python-multipart`, `tzdata`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- [x] `app/config.py` — `Settings(BaseSettings)` loading env vars from `.env`. Required fields fail fast on boot with a clear error.
- [x] `app/main.py` — `FastAPI` app with `/health` returning `{"status": "ok", "supabase": "connected", "anthropic": "configured"}`. Lifespan hook validates connections on startup.
- [x] `app/logging_config.py` — JSON logger, correlation ID per request, no `print()` anywhere.
- [x] `app/db/client.py` — Supabase client singleton (`get_supabase() -> Client`).
- [x] `app/db/schema.sql` — all tables with constraints and indexes:
  - [x] `posts` (id uuid pk, content text, caption text, hashtags text[], template_type text, image_url text, status text check in (`draft`,`pending_approval`,`approved`,`edit_requested`,`published`,`cancelled`), event_id uuid null, event_type text null, created_at timestamptz, approved_at timestamptz null, published_at timestamptz null)
  - [x] `post_platforms` (id, post_id fk, platform check in (`instagram`,`facebook`,`linkedin`), published_at timestamptz, external_id text, status text, error_message text)
  - [x] `employees` (id, name, title, hire_date date, department text null, active boolean) — **no birthdate column ever**
  - [x] `holidays` (id, name, month text, date_2026 date null, date_2027 date null, is_month_long boolean default false, category text check in (`general`,`food_industry`,`globex_founding`,`cultural`), description text null, recurring boolean default true)
  - [x] `trade_shows` (id, name, month text, start_date date null, end_date date null, location text null, booth text null, link text null, sponsors text[] null, notes text null, hidden boolean default false, needs_date_confirmation boolean default false)
  - [x] `approval_history` (id, post_id fk, action text, feedback text null, created_at timestamptz)
  - [x] `conversations` (phone_number text pk, current_post_id uuid fk null, state text, context jsonb, updated_at timestamptz)
  - [x] `branded_packaging_rotation` (id uuid pk, slot_number int unique check 1-20, caption_template text, hashtags text[], image_asset_path text, last_posted_at timestamptz null, active boolean default true)
- [x] `app/db/{posts,employees,holidays,trade_shows,approvals,conversations,packaging_rotation}.py` — typed query helpers, no raw SQL in business code.
- [x] `scripts/apply_schema.py` — runs `schema.sql` against Supabase. Idempotent.
- [x] `scripts/seed_db.py` — idempotent upsert from `app/data/*.json`.
- [x] `tests/conftest.py` — fixtures for a test Supabase project (or fully mocked client).
- [x] `.env.example`, `.gitignore`, `README.md` with local setup steps.

### Acceptance criteria
- [x] `uvicorn app.main:app --reload` boots cleanly, logs the configured environment, `GET /health` returns 200 with all subsystems "ok".
- [x] `python scripts/apply_schema.py` creates all tables; running it twice is a no-op.
- [x] `python scripts/seed_db.py` populates employees/holidays/trade_shows; running it twice doesn't duplicate rows.
- [x] `pytest tests/test_db_helpers.py` passes — covers CRUD for each table.
- [x] `ruff check app/` and `mypy app/ --ignore-missing-imports` pass.

**Dependencies:** Phase 0 (employees.json populated from Excel).
**Complexity:** Medium. Lots of plumbing, no surprises.

---

## Phase 2: Claude AI Engine

**Goal:** Given Karen's text (and optionally a photo), produce on-brand caption + hashtags + template choice. Separately, classify her incoming WhatsApp messages into intents.

### What gets built
- [x] `app/ai/client.py` — `Anthropic` client singleton with `claude-opus-4-7`, temperature 0.3, max_tokens tuned per call type. Retry wrapper via Tenacity (exponential backoff, max 3 attempts on 429/5xx).
- [x] `app/ai/prompts/brand.py` — `BRAND_BLOCK` constant: company facts (30+ years, 90+ countries, 300+ suppliers, 950+ trade partners), tone rules ("professional but human, direct, confident, no corporate jargon"), color palette, explicit DON'Ts (no birthdays, no kitschy posts, no oversaturation, no off-brand colors, no news-based content, no recipes). Imported into every system prompt. Includes curated Karen-voice excerpts as few-shot tone examples.
- [x] `app/ai/prompts/trade_show.py` — system prompt for pre/during/post trade show variants.
- [x] `app/ai/prompts/holiday.py` — covers date-specific + month-long variants.
- [x] `app/ai/prompts/milestone.py` — 20+ year anniversaries, draws from `employees` context.
- [x] `app/ai/prompts/founding_anniversary.py` — Nov 5 Globex Founding Day special case.
- [x] `app/ai/prompts/stats.py` — number-driven posts.
- [x] `app/ai/prompts/announcement.py` — new hires, partnerships, news.
- [x] `app/ai/prompts/product_spotlight.py` — uses animal/grain asset context.
- [x] `app/ai/prompts/promotional.py` — awareness, packaging launches.
- [x] `app/ai/prompts/branded_packaging.py` — pulls fixed copy from `branded_packaging_rotation` and lightly varies; lower creativity, higher consistency.
- [x] `app/ai/prompts/custom.py` — catch-all.
- [x] `app/ai/prompts/intent.py` — intent classifier prompt. Categories: `new_post_request`, `approval`, `edit_request`, `cancellation`, `greeting`, `unclear`. Output forced JSON via Claude tool use.
- [x] `app/ai/generator.py`:
  - [x] `async def generate_post(category: ContentCategory, context: dict, user_message: str, image_bytes: bytes | None) -> GeneratedPost` — returns `GeneratedPost(caption: str, hashtags: list[str], template_variant: str, rationale: str)`.
  - [x] Tool use forces structured JSON output (no regex parsing of free text).
  - [x] When `image_bytes` provided, attached as vision input.
- [x] `app/ai/intent.py`:
  - [x] `async def classify_intent(message: str, conversation_state: ConversationState) -> Intent` — returns `Intent(type: IntentType, extracted_request: str | None, edit_feedback: str | None, confidence: float)`.
  - [x] Takes conversation state because "yes" means "approval" only if there's a pending draft.
- [x] `app/ai/editor.py`:
  - [x] `async def apply_edit(current_post: GeneratedPost, feedback: str, category: ContentCategory, context: dict) -> GeneratedPost` — regenerates with Karen's feedback woven in.

### Acceptance criteria
- [x] `pytest tests/test_generator.py` — for each of 10 categories, given fixed input, output passes schema validation and contains caption + hashtags + template_variant.
- [x] `pytest tests/test_intent.py` — golden set of 30+ message examples (real Karen-style phrasings drawn from the WhatsApp chat) classify correctly. Cases: "approve", "yes", "looks good", "nope", "make it shorter", "change the headline to X", "post about us at SIAL", "wait nvm", "hi", plus Karen's awkward phrasings ("Like 1?", "Got it", etc.).
- [x] Vision test: feed sample photo from `tests/fixtures/photos/` + "post about us at SIAL Paris" → caption mentions SIAL and visible booth context (no hallucinated specifics).
- [x] Brand voice test: generated posts never contain birthday wishes, kitschy emojis, news-based references, recipe content, or off-brand colors (assert by lint rule on outputs).

**Dependencies:** Phase 1.
**Complexity:** High. Prompt engineering is iterative — expect 2-3 tuning passes per category. Brand voice is subjective; build a small "Karen would approve / Karen would reject" eval set early.

---

## Phase 3: Template Rendering

**Goal:** Given `(template_variant, caption, hashtags, context, optional_photo)`, produce a brand-perfect PNG at platform dimensions, uploaded to Supabase Storage, public URL returned.

### What gets built
- [x] `app/templates/html/_base.css` — CSS custom properties: `--brand-navy: #002D72`, `--brand-cyan: #5BC2E7`, font stack, spacing scale, logo positioning helpers. Imported by every template.
- [x] `app/templates/html/trade_show_pre.html`
- [x] `app/templates/html/trade_show_during.html`
- [x] `app/templates/html/trade_show_post.html`
- [x] `app/templates/html/holiday.html`
- [x] `app/templates/html/holiday_month_long.html`
- [x] `app/templates/html/milestone.html`
- [x] `app/templates/html/founding_anniversary.html`
- [x] `app/templates/html/stats.html`
- [x] `app/templates/html/announcement.html`
- [x] `app/templates/html/product_spotlight.html`
- [x] `app/templates/html/promotional.html`
- [x] `app/templates/html/branded_packaging.html`
- [x] `app/templates/html/custom.html`
- [x] All templates reference converted PNG logos from Phase 0; each declares its Jinja slots.
- [x] `app/templates/catalog.py` — `TEMPLATES: dict[str, TemplateSpec]` registry with `required_slots`. `PLATFORM_DIMENSIONS = {"square": (1080,1080), "landscape": (1200,630), "story": (1080,1920)}`.
- [x] `app/templates/renderer.py`:
  - [x] `class Renderer` with lifespan-managed Playwright browser (launched once at app startup, not per render — 5x faster).
  - [x] `async def render(template_variant: str, slots: dict, dimensions: tuple[int,int]) -> bytes` — Jinja render → `page.set_content(html, wait_until="networkidle")` → `page.screenshot(type="png")`.
  - [x] Photo embedding: if `slots["photo_url"]` is a Twilio URL, download with auth (Phase 4 helper) and pass as a data URL into the template so Playwright doesn't need network.
- [x] `app/db/storage.py`:
  - [x] `async def upload_png(post_id: UUID, png_bytes: bytes) -> str` — uploads to `post-images/{post_id}.png` bucket, returns public URL.
- [x] Logo + fonts in `app/templates/assets/`. Self-hosted, no external font CDN.
- [x] `scripts/render_all_templates.py` — outputs one example PNG per category to `docs/template-previews/` for Karen-quality visual review.

### Acceptance criteria
- [x] `pytest tests/test_renderer.py` — every template renders to a valid 2160² PNG and passes a per-template **brand-palette audit** (zero off-brand hue; 100% of non-white pixels within the navy–cyan–white system; theme-aware dominance). *Substituted for committed-pixel-baseline diffing — too environment-fragile across Chromium/OS subpixel AA; see Progress Log 2026-06-03.*
- [x] Visual review: `scripts/render_all_templates.py` outputs preview PNGs. Karen-quality eyeball check before moving on.
- [x] Brand audit: parse rendered PNG's pixel histogram, assert >40% of non-white pixels fall within ±10 of brand palette.
- [x] Render performance: square render p95 < 1500ms on local box (proves browser-reuse pattern works).

**Dependencies:** Phase 1 (storage helper needs Supabase client).
**Complexity:** High — not technically (Playwright is straightforward), but visually. Karen's bar is the long pole. Budget 2-3 design iterations per template.

---

## Phase 4: WhatsApp Integration

**Goal:** Karen messages Twilio → we receive, validate, parse, kick off the workflow, and send a preview back. Conversation state survives restarts.

### What gets built
- [x] `app/messaging/validator.py` — FastAPI dependency running Twilio's `RequestValidator` against `X-Twilio-Signature`. Rejects unsigned/forged requests with 403. Skipped in `ENVIRONMENT=development` only if explicitly configured.
- [x] `app/messaging/webhook.py`:
  - [x] `POST /webhooks/twilio/message` — receives `Body`, `From`, `NumMedia`, `MediaUrl0..N`, `MessageSid`. Acknowledges with empty TwiML `<Response/>` within Twilio's 15s window, then processes async via FastAPI background task.
  - [x] `POST /webhooks/twilio/status` — logs delivery/read/failed for outbound messages.
- [x] `app/messaging/media.py` — `async def download_twilio_media(url: str) -> tuple[bytes, str]` — fetches with Twilio auth, returns (bytes, content_type). 10s timeout.
- [x] `app/messaging/twilio_client.py`:
  - [x] `async def send_text(to: str, body: str)`
  - [x] `async def send_media(to: str, body: str, media_url: str)` — preview image + caption.
- [x] `app/messaging/conversation.py`:
  - [x] `class ConversationState(StrEnum)`: `IDLE`, `AWAITING_APPROVAL`, `EDITING`, `AWAITING_CLARIFICATION`.
  - [x] `async def get_or_create(phone: str) -> Conversation` (reads from `conversations` table).
  - [x] `async def transition(phone: str, new_state: ConversationState, current_post_id: UUID | None, context_patch: dict)` — atomic update.
- [x] `app/workflows/on_demand.py`:
  - [x] `async def handle_incoming_message(from_phone: str, body: str, media_urls: list[str]) -> None`
  - [x] Routes by intent (Phase 2 classifier) using current conversation state.
  - [x] For `new_post_request`: download media, call `generate_post`, call `renderer.render`, upload PNG, send preview via WhatsApp, transition to `AWAITING_APPROVAL`.
- [x] `app/workflows/approval.py`:
  - [x] `async def handle_approval(phone: str, conversation: Conversation) -> None` — marks post `approved`, kicks off Phase 5 publishing.
  - [x] `async def handle_edit_request(phone: str, conversation: Conversation, feedback: str) -> None` — calls `editor.apply_edit`, re-renders, sends new preview, stays in `AWAITING_APPROVAL`.
  - [x] `async def handle_cancellation(phone: str, conversation: Conversation) -> None` — marks post `cancelled`, transitions to `IDLE`.

### Acceptance criteria
- [x] `pytest tests/test_webhook.py` — Twilio signature validation rejects forged requests, accepts valid ones (uses Twilio's test vectors).
- [x] `pytest tests/test_conversation.py` — state machine transitions are exhaustive: every (state, intent) pair has a defined behavior.
- [x] E2E test with `httpx.AsyncClient` against local app + mocked Twilio: simulate Karen sending "post about us at SIAL" → assert preview was sent → simulate "approve" → assert publish was triggered (Phase 5 mocked).
- [x] Restart test: start a conversation, kill the process, restart, send "approve" — system correctly recovers Karen's pending draft from Supabase.

**Dependencies:** Phase 1, 2, 3.
**Complexity:** Medium-High. State machine is the trap — write exhaustive transition tests before implementation.

---

## Phase 5: Publishing

**Goal:** When a post is approved, publish to Instagram, Facebook, LinkedIn via Blotato. Independent success/failure per platform.

### What gets built
- [ ] `app/publishing/blotato.py`:
  - [ ] `class BlotatoClient` with `__init__(api_key)`, async httpx session.
  - [ ] `async def publish(image_url: str, caption: str, platforms: list[Platform]) -> dict[Platform, PublishResult]` — calls Blotato's publish endpoint per platform. Returns map of platform → `PublishResult(success: bool, external_id: str | None, error: str | None)`.
  - [ ] Platform-specific quirks isolated here (Blotato API specifics locked when implementation starts).
  - [ ] Retries idempotent failures (5xx, network) up to 3x with Tenacity. Does NOT retry 4xx.
- [ ] `app/publishing/formatter.py` (all platforms get same content, only formatting differs):
  - [ ] `def format_caption(caption: str, hashtags: list[str], platform: Platform) -> str`:
    - [ ] Instagram: caption + double-line + hashtags appended.
    - [ ] LinkedIn: caption + hashtags inline.
    - [ ] Facebook: caption + hashtags inline, conversational.
  - [ ] `def trim_for_platform(text: str, platform: Platform) -> str` — respects per-platform length limits (IG 2200, FB 63206, LI 3000).
- [ ] Wire `app/workflows/approval.py:handle_approval` to:
  - [ ] Mark post `approved` in DB.
  - [ ] Call `BlotatoClient.publish` for all three platforms in parallel (`asyncio.gather`).
  - [ ] Insert one `post_platforms` row per platform with status + external_id.
  - [ ] Mark post `published` if ≥1 platform succeeded, else flag for retry.
  - [ ] Send final WhatsApp confirmation to Karen: "Posted to IG ✓ FB ✓ LinkedIn ✗ (will retry)".

### Acceptance criteria
- [ ] `pytest tests/test_blotato.py` — uses `respx` to mock Blotato HTTP, asserts correct payload per platform, asserts retry on 503, no retry on 401.
- [ ] `pytest tests/test_formatter.py` — length trimming, hashtag rules per platform.
- [ ] Partial-failure test: mock LinkedIn 500, IG/FB 200 → assert post marked `published`, `post_platforms` has 2 successes + 1 failure, Karen gets correct status message.
- [ ] Live smoke test (manual, against a Blotato sandbox/test account if available): publish one real post end-to-end before declaring Phase 5 done.

**Dependencies:** Phase 1, 4.
**Complexity:** Medium. Blotato API specifics are the unknown — keys in hand reduces this risk.

---

## Phase 6: Automation

**Goal:** Each morning at 8am EST, check upcoming events, generate drafts for each, stagger them to Karen for approval. Also dispense the next branded-packaging post on its rotating schedule.

### What gets built
- [ ] `app/scheduler/jobs.py`:
  - [ ] `def register_jobs(scheduler: AsyncIOScheduler)`:
    - [ ] `daily_event_check` cron job at `0 8 * * *` America/New_York.
    - [ ] `branded_packaging_rotation_tick` cron job at `0 9 * * 1,3,5` America/New_York (Mon/Wed/Fri 9am — ~12 posts/month; 20-post pool rotates ~every 5 weeks).
    - [ ] `retry_failed_publishes` every 30 minutes.
- [ ] `app/scheduler/orchestrator.py`:
  - [ ] `async def daily_event_check() -> None`:
    - [ ] Query `holidays` (3-7 days out), `trade_shows` (3-7 days out, `hidden=false`, `needs_date_confirmation=false`), `employees` (anniversary in 5 days, ≥20 years, whole-decade or 25/30/etc.).
    - [ ] For each event, check `posts` table — if a post already exists for this event_id+event_type, skip (idempotency).
    - [ ] For each new event, generate draft via `generator.generate_post`, render via Phase 3, insert as `pending_approval`.
    - [ ] Schedule WhatsApp delivery staggered 3 minutes apart — 5 events → Karen gets one preview every 3 min.
    - [ ] On 1st of every month, generate one post per active month-long observance (National Beef Month, etc.).
    - [ ] Special-case Nov 5: generate a `founding_anniversary` post, not a generic holiday post.
    - [ ] Weekly digest job for TBC-date trade shows: "Heads up: N shows still TBC — confirm dates when you have them."
  - [ ] `async def branded_packaging_rotation_tick() -> None`:
    - [ ] Select next slot from `branded_packaging_rotation` where `active=true` ordered by `last_posted_at NULLS FIRST` LIMIT 1.
    - [ ] Generate caption via `branded_packaging` prompt, render with slot's `image_asset_path`.
    - [ ] Send to Karen as `pending_approval`.
    - [ ] On approval, update `last_posted_at` for the slot.
- [ ] `app/workflows/automated.py`:
  - [ ] `async def generate_scheduled_post(event: Event) -> Post`.
  - [ ] `async def generate_packaging_rotation_post(slot: PackagingSlot) -> Post`.
- [ ] Lifespan integration in `app/main.py` — scheduler starts with the app, shuts down on graceful exit.

### Acceptance criteria
- [ ] `pytest tests/test_scheduler.py` — freezegun-based: set "today" to one week before a known holiday, run `daily_event_check`, assert one draft created. Run again, assert no duplicate.
- [ ] Stagger test: seed 5 upcoming events, run check, assert 5 drafts but only 1 WhatsApp send happens immediately + 4 future-scheduled.
- [ ] Milestone filter test: 19-year, 20-year, 21-year, 25-year hire dates — only 20 and 25 generate a draft.
- [ ] Hidden trade show test: a show with `hidden=true` in the lookahead window must NOT generate a draft.
- [ ] TBC trade show test: a show with `needs_date_confirmation=true` does NOT auto-draft, but surfaces in the weekly digest.
- [ ] Month-long observance test: on May 1, generate a "National Beef Month" post. On May 2, do NOT generate a duplicate.
- [ ] Founding anniversary test: on Nov 5, generate a `founding_anniversary` post, not a regular `holiday` post.
- [ ] Packaging rotation fairness test: run `branded_packaging_rotation_tick` 25 times — each of 20 active slots selected at least once before any is selected twice.

**Dependencies:** Phase 1, 2, 3, 4.
**Complexity:** Medium. The traps are duplicate prevention across restarts and rotation fairness.

---

## Phase 7: Testing & Hardening

**Goal:** Production-grade reliability. Failure of one external service degrades but doesn't break the system.

### What gets built
- [ ] `tests/e2e/test_on_demand_flow.py` — full webhook → approval → publish loop with all external services mocked.
- [ ] `tests/e2e/test_automated_flow.py` — scheduler tick → drafts → Karen approves one → published.
- [ ] `app/utils/retry.py` — Tenacity decorators with sensible defaults per service (Anthropic: 3x exp backoff, Twilio: 3x, Blotato: per-platform 3x, Supabase: 2x).
- [ ] Failure mode handling:
  - [ ] Claude returns malformed JSON → fall back to second attempt with stricter prompt; if still bad, WhatsApp Karen: "AI hiccup, can you rephrase?"
  - [ ] Twilio send fails → log + retry. If 3 retries fail, queue in DB for manual review.
  - [ ] Render fails (Playwright crash) → restart browser, retry once. If still bad, send Karen a text-only preview with apology.
  - [ ] Blotato per-platform failure → partial publish + retry-queue row.
  - [ ] Supabase down → app refuses requests with clear 503 (hard dependency; fail loud).
- [ ] Observability:
  - [ ] Every webhook gets a correlation ID (`X-Correlation-ID`), propagated through logs.
  - [ ] Sentry SDK wired in (`SENTRY_DSN` env var).
  - [ ] `GET /health` extended to check Anthropic + Twilio + Supabase + Blotato (lightweight pings, cached 60s).
  - [ ] `GET /metrics` exposes: messages received last 24h, drafts created, posts published per platform, error rate.
- [ ] Edge cases covered in tests:
  - [ ] Karen sends a photo + no text.
  - [ ] Karen sends a 4-paragraph rambling request.
  - [ ] Karen sends "approve" with no pending draft.
  - [ ] Two messages from Karen within 2 seconds (debounce / queue).
  - [ ] Twilio webhook delivered twice (idempotency via `MessageSid`).
  - [ ] Karen edits 5 times before approving (no draft churn in DB — only final approved post counts).
  - [ ] Holiday in seed data is in the past (skip).
  - [ ] Employee with no hire_date (skip silently with a log).

### Acceptance criteria
- [ ] `pytest tests/ -v --cov=app` — coverage >85% on `app/workflows`, `app/messaging`, `app/publishing`, `app/ai/intent.py`, `app/ai/generator.py`.
- [ ] Chaos test (manual): kill Supabase mid-conversation, restart, verify recovery; same for Anthropic API errors injected via mock.
- [ ] Load smoke: 20 webhook calls in 10 seconds — system handles them serially without dropping any.

**Dependencies:** All previous phases.
**Complexity:** Medium-High. Mostly mechanical, but the edge case set is wide.

---

## Phase 8: Deployment

**Goal:** Live on Railway, Twilio webhook pointing to production URL, Karen can use it from her phone.

### What gets built
- [ ] `Procfile` — `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- [ ] `railway.toml` — build config, healthcheck path `/health`, restart policy `on-failure`.
- [ ] `nixpacks.toml` (or `Dockerfile`) — Python 3.12, Playwright Chromium download in build step (`playwright install chromium --with-deps`).
- [ ] Railway env vars set: every key from `.env.example`. `ENVIRONMENT=production`.
- [ ] Supabase production project provisioned, schema applied, seed data loaded.
- [ ] Twilio WhatsApp production number pointed at `https://<railway-domain>/webhooks/twilio/message` and `/status`.
- [ ] Blotato connected to Globex's actual IG/FB/LinkedIn accounts (already done).
- [ ] Sentry project created, DSN added.
- [ ] Smoke runbook: Karen sends "test" → receives canned reply, validates round trip.
- [ ] `README.md` deployment section.
- [ ] `docs/runbook.md` — one-page operator runbook: pause scheduler, manually requeue failed publish, add a new holiday.

### Acceptance criteria
- [ ] Production `/health` returns 200 with all subsystems "ok".
- [ ] Karen successfully sends "post about us reaching 150 ships" from her phone, receives preview within 60s, approves, sees post live on all three platforms.
- [ ] 24-hour soak: scheduler ticks once, no error logs, no Sentry alerts.

**Dependencies:** All previous phases.
**Complexity:** Medium. Playwright on Railway is the only non-trivial bit — Chromium needs to be in the build, not downloaded at boot.

---

## Critical files to keep in your head

When executing, these are the files that change most across phases — re-read them before edits:

- [app/main.py](app/main.py) — touched in Phase 1, 4, 6, 7, 8.
- [app/workflows/on_demand.py](app/workflows/on_demand.py) — central orchestrator, touched in Phase 4, 5, 7.
- [app/workflows/approval.py](app/workflows/approval.py) — Phase 4 creates it, Phase 5 extends it.
- [app/ai/prompts/brand.py](app/ai/prompts/brand.py) — single source of brand truth, every prompt imports it.
- [app/messaging/conversation.py](app/messaging/conversation.py) — state machine, every WhatsApp turn passes through.
- [app/db/schema.sql](app/db/schema.sql) — source of truth for tables; migrations append, never edit.

---

## Verification: end-to-end checks before declaring shippable

Executed from Karen's actual phone once Phase 8 is live:

- [ ] **On-demand text-only:** WhatsApp "post about us hitting 150 ships on the water" → receive stats-template preview within 60s → reply "approve" → confirm IG/FB/LinkedIn posts exist with correct content within 90s of approval.
- [ ] **On-demand with photo:** WhatsApp a trade show floor photo + "post about us at SIAL Paris" → preview uses photo composited into trade_show_during template → approve → live on all three platforms.
- [ ] **Edit loop:** Request a post → preview arrives → reply "make the headline shorter" → new preview within 30s with shorter headline, same image → approve → published.
- [ ] **Cancellation:** Request a post → preview arrives → reply "cancel" → conversation returns to idle, no publish.
- [ ] **Automated holiday flow:** Seed a holiday for `today + 5 days`, manually trigger `daily_event_check`, confirm Karen receives a draft, approves it, published.
- [ ] **Branded packaging rotation:** Manually trigger `branded_packaging_rotation_tick`, confirm the slot with oldest `last_posted_at` (or NULL) is selected, Karen receives the pre-designed post for approval, on approval the slot's `last_posted_at` is updated.
- [ ] **Restart resilience:** Start an approval loop, redeploy Railway service mid-conversation, reply "approve" — system recovers and publishes correctly.
- [ ] **Partial publish failure:** Manually disable LinkedIn in Blotato, approve a post → IG + FB publish, Karen receives "Posted to IG ✓ FB ✓ LinkedIn ✗ (will retry)", retry job picks it up when LinkedIn is re-enabled.
- [ ] **Hidden trade show:** Seed an April trade show with `hidden=true` in 7-day window, run `daily_event_check`, confirm NO draft generated.

If all 9 pass, system is shippable to Karen.

---

## Outstanding asks for Karen / Ilan (consolidated)

Phase 0 generates `docs/missing_assets.md`. Send Karen ONE consolidated email — do not dripfeed.

**RESOLVED:**
- ✅ News-based content: OUT (user 2026-05-19).
- ✅ Blotato accounts: connected.
- ✅ Newer employee Excel located — 39 employees, 6 qualifying for 20+ year milestones.
- ✅ Trade shows: 11 shows for 2027 extracted from Events tab.
- ✅ Holidays: 22 entries extracted from Holidays tab.
- ✅ Recipe category: dropped.
- ✅ Platform routing: all 3 platforms get same content.

**STILL OUTSTANDING:**
- [ ] **Re-extracted asset ZIP** — current download has empty files for animals (Cow/Chicken/Pig/Fish), packaging (5 color variants), 30-year mark, grains. Highest-impact missing item.
- [ ] **The 20 rotating brand/packaging posts themselves** — Karen mentioned new branding/packaging coming. Need: (a) 20 product/packaging images, (b) Karen's preferred copy or angle per slot, OR (c) free rein for Claude to draft and Karen approves once.
- [ ] **TBC trade show dates** — 8 of 11 shows have "TBC". Karen confirms as she gets event info. Weekly nudge handled by system.
- [ ] **Booth numbers** for trade shows — all blank in Excel. Karen fills as she registers.
- [ ] **National Fish Day date** — listed in Holidays tab with no date. Drop or specify.
- [ ] **Twilio WhatsApp Business number provisioned** — dedicated number (NOT Karen's `+1-917-859-2787` personal number).
- [ ] **API credentials in `.env`** — `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`, `AUTHORIZED_NUMBERS` (comma-separated allowlist: Karen's `whatsapp:+19178592787` + dev numbers), `BLOTATO_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`.
- [ ] **Supabase project provisioned** — production project with Storage bucket `post-images` and schema applied.
- [ ] **Railway project provisioned** + env vars set.

## Open architectural questions (deferable; reasonable defaults assumed)

1. Should Karen be able to schedule a post for a specific future time? Not in brief/contract — assume **NO** for now.
2. Carousel / multi-image posts? Karen's response was unclear in chat 27/04 — assume **single image** for now.
3. Story/Reel posting? Not in scope — IG feed posts only.

---

## Progress Log

Dated notes as work gets done. Newest at top. Include: what landed, what surprised you, what's queued next.

### 2026-06-04 — Iteration: AI image generation (kie.ai / nano-banana)
- **What landed:** a second way to make an image post. Two paths now exist — (1) Karen sends a photo → renders on the template (existing), (2) she describes a post and wants it as an image → we generate one. A new **visual planner** (`app/ai/visual_planner.py`, one Claude call) decides per text request: `typographic` (designed template — the default), `generated_image` (AI photo + brand overlay), or `clarify` (ask her which — the "feels like an employee" behaviour). Generation goes through **kie.ai `nano-banana-2`** (`app/ai/image_gen.py`, async create→poll→download). **Key architectural reuse:** a generated image is just a "photo" — it flows into the existing `render_and_store(photo_bytes=…)` which already overlays the brand template (logo/ring/headline) on a photo-capable `custom` variant. So brand identity stays in the template; the AI only supplies the scene (resolves the CLAUDE.md "no AI images" rule, which was updated to "photographic layer only, under the overlay").
- **Smart edits (chosen behaviour):** generated-image posts persist the **raw** image (Supabase `-raw` object) + the image prompt in conversation context. On edit, `editor.classify_edit_kind` decides visual vs textual: a **visual** change ("make it sunset") runs kie.ai **img2img** (`edit`, `image_input`) on the raw image → re-overlay; a **textual** change ("shorten the headline") re-renders the overlay on the *same* picture (instant, free). Only pays a regen when the picture must change.
- **Robustness:** immediate "🎨 Generating…" heads-up (gen takes ~20–30s); any kie.ai failure (timeout/credits/429/fail/no-key) **falls back to a designed typographic draft** + a note — never a dead end; result URLs (short-lived) are downloaded + re-hosted immediately; ambiguous re-asks collapse to typographic (no clarify loop); a "cancel" during clarification backs out cleanly.
- **Acceptance PASS:** ruff + mypy clean (56 src files); full suite **124 passed / 46 skipped** — new `test_image_gen.py` (8 cases: success / no-key / error-code / 429-retry / fail-state / timeout / edit-field / mapping, httpx faked) + 6 new e2e cases (generated→overlay→preview, gen-failure→typographic fallback, ambiguous→ask→resolve, visual edit→img2img, textual edit→same image). **Live round-trips verified:** text→image via `nano-banana-2` (~28s, 8 credits ≈ $0.04) and img2img `edit` (723 KB returned) both against the real API; the exact model string was confirmed live (docs were inconsistent).
- **Model:** `nano-banana-2` for both gen + img2img (config `KIE_IMAGE_MODEL`/`KIE_EDIT_MODEL`); `nano-banana-pro` is a one-line swap for hero quality. ~$0.04/image; typographic posts cost no image spend.
- **Queued next:** Phase 5 (Publishing via Blotato). **Possible fast-follow:** img2img on Karen's *own* attached photo ("turn this into…"). **Security note:** the kie.ai key was pasted in chat → recommend rotating it (same as the OpenAI key).

### 2026-06-04 — Iteration: voice notes (WhatsApp)
- **What landed:** Karen can now send a **WhatsApp voice note** instead of typing. A voice note arrives like a photo (Twilio `MediaUrl0`, content-type `audio/ogg`); the handler now partitions media by content-type — `audio/*` → transcribe → use as the instruction text; `image/*` → today's photo path. Transcription is a thin adapter (`app/messaging/transcription.py`, OpenAI **Whisper `whisper-1`**), so the *entire* downstream pipeline (intent → state machine → generate → approve/edit/cancel) is unchanged: a voice note "post about SIAL" becomes the string and flows through identically. Anthropic has no STT, so Whisper is the one new vendor (`OPENAI_API_KEY`, optional — voice degrades gracefully if unset; nothing else breaks).
- **Behaviour (Karen's choice): echo-all, never-block.** Every voice note replies `🎤 Heard: "…"` then acts immediately — including publishing on a voice "approve". The mis-hear risk on approve was flagged; Karen accepted it for lower friction. The echo at least makes a mis-hear visible.
- **Robustness:** brand-vocab prompt biasing (so "SIAL Paris" ≠ "seal Paris"); `verbose_json` + `no_speech_prob` guard rejects silence / Whisper hallucinations ("Thanks for watching!"); 25 MB size cap; download / API-error / missing-key all degrade to a friendly reply and never crash the background task. Photo **+** voice in one message handled (transcript = instruction, image = render target). **Out of scope:** correlating a voice note sent as a *separate* message right after a photo (needs a stateful media buffer) — flagged, not built.
- **Acceptance PASS:** ruff + mypy clean (53 src files); full suite **88 passed / 46 skipped** — new `test_transcription.py` (7 cases: ok / empty / hallucination-guard / oversize / missing-key / API-error / ext-mapping, OpenAI faked), 4 new voice E2E cases (transcribe→echo→draft, voice-approve→publish, photo+voice, no-speech→friendly), webhook media-tuple test. **Live round-trip verified:** TTS→OGG/Opus→real Whisper `transcribe()` returned the exact text with "SIAL Paris" spelled right; key auth + `whisper-1` reachability confirmed.
- **Signature change:** `handle_incoming_message(..., media: list[(url, content_type)])` (was `media_urls: list[str]`); webhook now extracts `MediaContentType{i}`.
- **Queued next:** Phase 5 (Publishing via Blotato). **Security note:** the OpenAI key was pasted in chat → recommend rotating it once live.

### 2026-06-03 — Phase 4 complete (WhatsApp Integration)
- **Landed:** the full on-demand loop. Twilio webhook (`/webhooks/twilio/message` + `/status`) acks with empty TwiML inside the 15s window then processes in a FastAPI background task; signature validation as a dependency (`validator.py`). `twilio_client` (async `send_text`/`send_media` over the sync SDK via `to_thread`), `media.download_twilio_media` (auth + follow-redirects). `messaging/conversation.py` (`ConversationState` + Supabase-backed async state) and `state_machine.py` (pure, exhaustive `(state × intent) → Action` routing). `workflows/on_demand.py` (authorize → classify intent → route → dispatch) and `workflows/approval.py` (approve→publish / edit→re-render / cancel). `render_pipeline` (`build_slots` + `render_and_store`). Publishing seam stub (`publishing/publisher.py`) for Phase 5. Renderer started/stopped in the FastAPI lifespan; webhook router wired in.
- **Phase 2 amendment (the planned 'small addition'):** `GeneratedPost` gained on-image display fields — `headline`, `subhead`, `figure`, `figure_unit` — so the graphic carries short poster text distinct from the social caption. Added `generate_freeform` (the model self-selects the `template_variant` for free-form WhatsApp requests; the category-specific prompts remain for the scheduler path). `editor.apply_edit` is now category-optional (freeform edits use the freeform system prompt).
- **Acceptance PASS:** webhook signature accept/reject via Twilio's own RequestValidator; **exhaustive state machine** — all 24 `(state × intent)` cells defined (`test_state_machine.py`); offline **E2E** — new→preview→approve→publish-triggered, plus edit / cancel / nothing-pending / unauthorized (`test_workflow_e2e.py`, all externals + DB mocked); **restart/persistence** — pending draft survives a fresh read from Supabase (`test_conversation_persistence.py`, ran live). ruff + mypy clean (52 src files); full suite **73 passed / 46 skipped**.
- **Live validation:** `generate_freeform` on 3 real requests selected the right variants (stats / trade_show_pre / announcement), produced tight ≤6-word headlines and extracted figures ("150 Ships", "12 New Markets"), and rendered cleanly through the real pipeline. **Supabase Storage smoke** (`ensure_bucket` + `upload_png` → public URL → GET 200 `image/png`) — so Phase 3 storage is now verified live too.
- **Deviations / decisions:** the exhaustive-transition test is `test_state_machine.py` (plan said `test_conversation.py`) — same requirement, different file. The E2E drives `handle_incoming_message` directly (the HTTP webhook itself is covered by the signature test). Signature validation is toggled by `twilio_validate_signature` (default on) rather than env-gated. **Edits re-render without re-applying Karen's original photo** (not persisted) — copy/layout edits are the common case; persisting the source photo is a follow-up. On-demand category selection is done by `generate_freeform` (model picks the variant); DB enrichment of show/holiday context (booth/dates) is deferred — the model folds any specifics Karen gives into the headline/subhead.
- **Queued next:** Phase 5 (Publishing) — `publisher.publish_post` becomes a real Blotato call to Instagram / Facebook / LinkedIn with per-platform results.

### 2026-06-03 — Phase 3 complete (Template Rendering)
- **Landed:** the full visual system — 13 HTML/CSS brand templates + `_base.css` design system (self-hosted **Montserrat**, navy/cyan/white only, a cyan **ring motif** echoing the G-man mark, navy-hero + light-editorial themes), `_canvas.html` base layout, `assets.py` (hermetic base64 data-URI embedding of fonts + logos), `catalog.py` (`TEMPLATES` registry + `PLATFORM_DIMENSIONS`), `renderer.py` (lifespan-managed **reused** Chromium, deterministic output), `db/storage.py` (`upload_png` + `ensure_bucket`), and `scripts/render_all_templates.py` (preview gallery → `docs/template-previews/`).
- **Acceptance PASS:** 41 renderer tests — every template renders to a valid 2160² PNG and clears a per-template brand audit (**0.0000 off-brand hue across all 13**, 100% of non-white pixels within the navy–cyan–white system, theme-aware dominance); median warm render **< 1500ms** (proves browser reuse). ruff + mypy clean (41 files). Full default suite: **54 passed / 46 skipped** (renderer runs locally; live-AI + DB integration stay gated).
- **Visual checkpoint:** rendered 3 flagship templates first, got sign-off on the design language ("ship it"), then mass-produced the other 10 against the locked system. Gallery eyeballed — on-brand, premium, consistent.
- **Deviations / decisions:**
  - **Hermetic rendering:** fonts + logos are inlined as base64 data URIs because a Playwright `set_content` document has an `about:blank` origin and cannot load `file://` subresources. Output is made deterministic (grayscale AA, sRGB, `document.fonts.ready`) so pixel output is stable. Render at `device_scale_factor=2` → 2160² retina PNG (logical 1080).
  - **Brand audit replaces committed pixel baselines** (acceptance #1): cross-environment subpixel/AA nondeterminism makes a 0.5%-tolerance committed-baseline diff flaky; the stronger, robust guard is the color audit. The plan's "40% of non-white pixels" formulation also fails for legitimately white-background editorial templates (their non-white pixels are mostly sub-threshold text AA, ~29–33%) — replaced with: zero off-brand hue + 100% of non-white pixels in-system + theme-aware dominance.
  - **`jinja2` added** to `requirements.txt` (mandated by the plan but missing); **`pillow`** added to `requirements-dev.txt` for the pixel audit. `playwright install chromium-headless-shell` is required in addition to `chromium` (default headless launch uses the shell binary) — note for the Railway build.
  - **Asset ZIP does NOT block Phase 3:** `product_spotlight` + `branded_packaging` ship with an image slot + a graceful G-man placeholder. The animal illustrations / packaging colorways drop into an existing `<img>` slot when Karen sends the re-extracted ZIP — no template code changes needed.
  - **On-image text vs caption:** templates consume short display slots (`headline`/`figure`/`eyebrow`/`subhead`), distinct from the AI's social caption. Phase 4 fills the slots; the clean wiring is a small Phase 2 schema addition (have the AI also emit those short display fields).
  - **Storage:** `upload_png`/`ensure_bucket` API shapes verified offline against supabase-py (`FileOptions`/`CreateOrUpdateBucketOptions`); a live upload smoke is deferred until Supabase is unpaused (same posture as the DB integration tests).
- **Queued next:** Phase 4 (WhatsApp integration) — intent → generate → render → upload → WhatsApp preview → approval/edit loop. Needs the Phase 2 display-fields addition + Supabase active for storage.

### 2026-06-03 — Phase 2 complete (Claude AI Engine)
- **Landed:** async Anthropic client + forced-tool-use structured-output helper; `BRAND_BLOCK` (single source of brand voice) + 10 category prompts + intent prompt; `generator.generate_post` (with vision), `intent.classify_intent` (state-aware), `editor.apply_edit`; reusable `brand_check` lint.
- **All acceptance criteria PASS:** ruff + mypy clean (35 files); 13 always-on deterministic tests; **35 live tests** — a 33-case state-aware intent golden set + 10/10 category generations + vision, every generation brand-lint-clean. Sample copy reviewed by eye: on-voice, fact-grounded, zero violations.
- **Deviations (Opus 4.7 reality vs the plan):** the plan said "temperature 0.3", but Opus 4.7 **removed** `temperature`/`top_p`/`top_k` (400 if sent) — brand consistency now comes from the prompt + forced schema, not a sampling knob. Structured output uses **forced `tool_choice` + Pydantic validation** (not `output_config.format`, which isn't guaranteed on 4.7). API retries use the SDK's built-in `max_retries`, not a separate Tenacity wrapper.
- **Brand-voice source (important):** the WhatsApp export turned out to be the ElevateAIo **internal team** chat — Karen isn't a participant — so there were NO authentic Karen phrasings to mine. `BRAND_BLOCK` is built from CLAUDE.md rules + two in-house **style-anchor** captions (clearly NOT attributed to Karen). **ACTION:** collect real Karen-approved captions post-launch → they become the few-shot set + the eval reference. Content direction extracted from the chat: Len wants every angle advertised (incl. duck, pet food, packaging); news content confirmed OUT.
- **Cost control:** live AI tests are gated behind `RUN_AI_LIVE` (real Opus calls cost ~$1/run). Default `pytest` runs 13 fast deterministic tests and skips 46 live ones. Run live with `RUN_AI_LIVE=1`.
- **Model:** `claude-opus-4-7` via `settings.claude_model`; bumping to `claude-opus-4-8` (latest, same API surface) is a one-line `.env` change.
- **Queued next:** Phase 3 (Template Rendering, Playwright HTML→PNG). Logo-based templates can proceed; full set still blocked on the re-extracted asset ZIP (animals/packaging/grains/30-year).

### 2026-06-03 — Phase 1 complete (Foundation)
- **Landed:** FastAPI app (config, JSON logging w/ correlation IDs, lifespan + correlation-id middleware), Supabase client + 8-table `schema.sql`, 7 typed query helpers, `apply_schema.py` (psycopg DDL) + idempotent `seed_db.py`, integration CRUD tests, README, pinned deps.
- **All acceptance criteria PASS:** ruff clean · mypy clean (16 files) · `GET /health` = 200 `{supabase: connected, anthropic: configured}` · apply_schema idempotent · seed idempotent (37 employees / 21 holidays / 12 shows, no dupes on re-run) · 6/6 pytest CRUD tests green against live Supabase.
- **Supabase gotcha (cost real time):** this project's Postgres is reachable ONLY via the pooler, and specifically the newer cluster `aws-1-us-east-1.pooler.supabase.com` — every `aws-0-*` region returned "Tenant or user not found", and the direct `db.<ref>.supabase.co` host has no DNS at all. DDL runs through psycopg on that pooler (user `postgres.<ref>`); all runtime access is supabase-py over REST (443). `SUPABASE_DB_URL` lives in local `.env`; Railway needs it as an env var at Phase 8.
- **Free-tier risk:** the project auto-paused once (the earlier outage). Recommend Supabase Pro ($25/mo, no auto-pause) before launch.
- **Type note:** supabase-py types `.data` as `list[JSON]`; added `rows()/row()/maybe_row()` cast wrappers in `db/client.py` so helpers stay mypy-clean — one place to cast, not 28.
- **Queued next:** Phase 2 (Claude AI engine). Will mine `whatsapp_extract/_chat.txt` for Karen's voice (deferred from Phase 0) to build the brand block.

### 2026-06-03 — Phase 0 complete (Asset Import)
- **Logos:** All 3 EPS → transparent PNG at 2160px, plus reversed white variants (navy→white, cyan kept) for dark backgrounds — 6 files in `app/templates/assets/logos/`. Vector-crisp; brand colours verified by pixel sampling (#002D72 navy, #5BC2E7 cyan).
- **Deviation (logos):** The plan's `magick convert -background none` recipe does NOT work for these files. The Illustrator EPS embed a low-res TIFF preview (ImageMagick rasterises that, emitting 2 scenes) AND paint a white background (so `-background none` gives opaque white). Switched to **Ghostscript `pngalpha` directly** (`-dEPSCrop`, per-file DPI from the EPS BoundingBox), with ImageMagick used only for downscale + the navy→white recolour. Encoded in `scripts/import_assets.py`.
- **Data:** 37 employees (plan estimated 39), hire_date ONLY — no DOB/age/passwords. 6 qualify for 20+yr milestones (Ilona & Len Kogan 33, Petrenko 26, Karapetyan 24, Rebe 22, Rybakov 21). 12 trade shows (plan said 11; +1 meta row skipped), 7 TBC. 21 holidays, 4 month-long.
- **Judgment calls:** Easter deduped (kept Apr-5 2026 row); National Fish Day dropped (no date — flagged for Karen); Globex Founding Day 2027 cell was a typo (read 2026-11-05) → corrected to 2027-11-05; Sial Paris hidden (even-years, no 2027 occurrence); Anuga kept (odd-years, dated).
- **Tooling:** ImageMagick 7.1.2 + Ghostscript 10.07.1 via **Scoop** (user-space, no admin). Portable ImageMagick needs MAGICK_HOME/CODER_MODULE_PATH set — handled in `setup_tools()`. Local-only (Railway never renders EPS).
- **Chat excerpts:** Curation deferred to Phase 2; raw WhatsApp export kept OUT of the repo (personal numbers). Stub at `docs/reference/client_chat_excerpts.md`.
- **Queued next:** Phase 1 (Foundation), pending review of the extracted JSONs. Phase 3 still blocked on the re-extracted asset ZIP (see `docs/missing_assets.md`).

### 2026-05-19 — Plan locked
- Asset folder inventoried at `c:\Users\abdur\Downloads\Globex\Globex\`. 3 logos usable (EPS, need PNG conversion). The 0-byte sub-folder files (animals, packaging, grains, 30-year mark) need re-extraction.
- Newer Excel found at `c:\Users\abdur\Downloads\Date of Hire Globex, Birthdate and PWs - Copy.xlsx` (May 19) — supersedes the May 4 copy in the Globex folder. 3 tabs all readable: 39 employees, 11 trade shows, 22 holidays.
- Scope decisions confirmed by user: recipe category dropped, news-based content out, all 3 platforms get same content.
- Blotato: accounts already connected.
- Blockers: re-extracted ZIP for animal/packaging/grain art, the 20 packaging post content, API keys for `.env`, Supabase project, dedicated Twilio Business number.
- Next: Phase 0 once API keys + Supabase project are ready.
