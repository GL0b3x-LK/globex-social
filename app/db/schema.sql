-- Globex SM Automation — database schema (source of truth).
-- APPEND-ONLY: never edit an existing statement; add a migration in migrations/.
-- Idempotent: safe to run repeatedly (CREATE TABLE IF NOT EXISTS, etc.).
-- Applied by scripts/apply_schema.py against SUPABASE_DB_URL.

-- gen_random_uuid() ships with Postgres 13+ (pgcrypto). Ensure it's available.
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- posts — every generated post, through its approval lifecycle.
-- ---------------------------------------------------------------------------
create table if not exists posts (
    id            uuid primary key default gen_random_uuid(),
    content       text,
    caption       text,
    hashtags      text[]      not null default '{}',
    template_type text,
    image_url     text,
    status        text        not null default 'draft'
                  check (status in ('draft','pending_approval','approved',
                                    'edit_requested','published','cancelled')),
    event_id      uuid,
    event_type    text,
    created_at    timestamptz not null default now(),
    approved_at   timestamptz,
    published_at  timestamptz
);
create index if not exists idx_posts_status on posts (status);
create index if not exists idx_posts_event on posts (event_type, event_id);

-- ---------------------------------------------------------------------------
-- post_platforms — per-platform publish result (independent success/failure).
-- ---------------------------------------------------------------------------
create table if not exists post_platforms (
    id            uuid primary key default gen_random_uuid(),
    post_id       uuid        not null references posts (id) on delete cascade,
    platform      text        not null
                  check (platform in ('instagram','facebook','linkedin')),
    published_at  timestamptz,
    external_id   text,
    status        text        not null default 'pending',
    error_message text,
    unique (post_id, platform)
);
create index if not exists idx_post_platforms_post on post_platforms (post_id);

-- ---------------------------------------------------------------------------
-- employees — hire_date ONLY. No birthdate / age / password columns, ever.
-- ---------------------------------------------------------------------------
create table if not exists employees (
    id         uuid primary key default gen_random_uuid(),
    name       text    not null unique,
    title      text,
    hire_date  date,
    department text,
    active     boolean not null default true
);
create index if not exists idx_employees_active on employees (active);

-- ---------------------------------------------------------------------------
-- holidays — general + food-industry days, month-long observances, founding.
-- ---------------------------------------------------------------------------
create table if not exists holidays (
    id            uuid primary key default gen_random_uuid(),
    name          text    not null unique,
    month         text,
    date_2026     date,
    date_2027     date,
    is_month_long boolean not null default false,
    category      text    not null default 'general'
                  check (category in ('general','food_industry','globex_founding','cultural')),
    description   text,
    recurring     boolean not null default true
);
create index if not exists idx_holidays_month on holidays (month);

-- ---------------------------------------------------------------------------
-- trade_shows — 2027 show calendar; TBC + hidden handled gracefully.
-- ---------------------------------------------------------------------------
create table if not exists trade_shows (
    id                      uuid primary key default gen_random_uuid(),
    name                    text    not null unique,
    month                   text,
    start_date              date,
    end_date                date,
    location                text,
    booth                   text,
    link                    text,
    sponsors                text[],
    notes                   text,
    hidden                  boolean not null default false,
    needs_date_confirmation boolean not null default false
);
create index if not exists idx_trade_shows_lookahead
    on trade_shows (start_date) where hidden = false and needs_date_confirmation = false;

-- ---------------------------------------------------------------------------
-- approval_history — append-only audit of state transitions.
-- ---------------------------------------------------------------------------
create table if not exists approval_history (
    id         uuid primary key default gen_random_uuid(),
    post_id    uuid        not null references posts (id) on delete cascade,
    action     text        not null,
    feedback   text,
    created_at timestamptz not null default now()
);
create index if not exists idx_approval_history_post on approval_history (post_id);

-- ---------------------------------------------------------------------------
-- conversations — per-phone WhatsApp state. Survives Railway restarts.
-- ---------------------------------------------------------------------------
create table if not exists conversations (
    phone_number    text primary key,
    current_post_id uuid references posts (id) on delete set null,
    state           text        not null default 'idle',
    context         jsonb       not null default '{}'::jsonb,
    updated_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- messages — full WhatsApp transcript (both directions). Powers persistent
-- conversation memory AND swipe-to-reply (map a Twilio reply SID back to a post).
-- Append-only log; kept in Supabase, never in the repo.
-- ---------------------------------------------------------------------------
create table if not exists messages (
    id          uuid primary key default gen_random_uuid(),
    phone_number text       not null,
    twilio_sid  text,
    role        text        not null check (role in ('karen','agent')),
    body        text,
    media_url   text,
    kind        text        not null default 'text'
                check (kind in ('text','voice','image','preview')),
    post_id     uuid        references posts (id) on delete set null,
    created_at  timestamptz not null default now()
);
create index if not exists idx_messages_phone_time on messages (phone_number, created_at);
create index if not exists idx_messages_twilio_sid on messages (twilio_sid);

-- Full render inputs for a post (display fields, treatment, image prompt, raw
-- image URL) so a post can be re-opened later (swipe-reply redesign/repost) and
-- edited exactly as when it was first drafted.
alter table posts add column if not exists render_meta jsonb;

-- Which platforms this post should publish to (null = all connected). Lets Karen
-- say "post this only to LinkedIn" and have it skip the others.
alter table posts add column if not exists target_platforms text[];

-- ---------------------------------------------------------------------------
-- branded_packaging_rotation — finite pool of 20 pre-designed posts.
-- ---------------------------------------------------------------------------
create table if not exists branded_packaging_rotation (
    id               uuid primary key default gen_random_uuid(),
    slot_number      int     not null unique check (slot_number between 1 and 20),
    caption_template text,
    hashtags         text[]  not null default '{}',
    image_asset_path text,
    last_posted_at   timestamptz,
    active           boolean not null default true
);

-- ===========================================================================
-- VIDEO ENGINE (goodwill layer) — see globex-video-engine-plan.md.
-- Seed data lives in app/data/characters.json + products.json; these tables are
-- the durable home once SUPABASE_DB_URL is configured. The library modules read
-- JSON today, so applying this schema is not a prerequisite for V0/V1.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- characters — the named personas who front videos. Invented by default; a real
-- person requires recorded likeness consent (is_real_person + consent columns).
-- Only status='approved' characters may appear in a generated video.
-- ---------------------------------------------------------------------------
create table if not exists characters (
    id                   uuid primary key default gen_random_uuid(),
    slug                 text    not null unique,
    name                 text    not null unique,
    full_name            text,
    aliases              text[]  not null default '{}',
    gender               text,
    ethnicity            text,
    age                  int,
    role                 text,
    persona              text,
    speaking_style       text,
    appearance_notes     text,
    setting_affinity     text[]  not null default '{}',
    market_tags          text[]  not null default '{}',
    visual_prompt        text,
    voice_direction      text,
    voice_id             text,
    reference_image_urls text[]  not null default '{}',
    provider_refs        jsonb   not null default '{}'::jsonb,
    is_real_person       boolean not null default false,
    likeness_consent     jsonb,
    status               text    not null default 'draft'
                         check (status in ('draft','approved','retired')),
    created_at           timestamptz not null default now()
);
create index if not exists idx_characters_status on characters (status);

-- ---------------------------------------------------------------------------
-- products — the master product list Globex never had. claims_forbidden and
-- visual_rules make the client's content rules enforceable data, not prose.
-- ---------------------------------------------------------------------------
create table if not exists products (
    id                 uuid primary key default gen_random_uuid(),
    slug               text    not null unique,
    name               text    not null unique,
    aliases            text[]  not null default '{}',
    category           text,
    description        text,
    formats            text[]  not null default '{}',
    pack_shot_urls     text[]  not null default '{}',
    product_shot_urls  text[]  not null default '{}',
    talking_points     jsonb   not null default '[]'::jsonb,
    claims_forbidden   text[]  not null default '{}',
    visual_rules       jsonb   not null default '{}'::jsonb,
    markets            text[]  not null default '{}',
    status             text    not null default 'active'
                       check (status in ('active','retired')),
    created_at         timestamptz not null default now()
);
create index if not exists idx_products_status on products (status);

-- ---------------------------------------------------------------------------
-- broll_assets — REAL footage beats generated footage; the engine prefers a
-- tag-matched real clip over an AI b-roll scene whenever one exists.
-- ---------------------------------------------------------------------------
create table if not exists broll_assets (
    id          uuid primary key default gen_random_uuid(),
    name        text    not null unique,
    url         text    not null,
    kind        text    not null default 'video' check (kind in ('video','image')),
    tags        text[]  not null default '{}',
    seconds     numeric,
    source      text,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);
create index if not exists idx_broll_active on broll_assets (active);

-- ---------------------------------------------------------------------------
-- videos — one row per requested video, through its approval lifecycle.
-- Mirrors the posts table's discipline: nothing publishes without approval.
-- ---------------------------------------------------------------------------
create table if not exists videos (
    id                     uuid primary key default gen_random_uuid(),
    brief                  text,
    mode                   text        not null default 'presenter'
                           check (mode in ('presenter','voiceover')),
    character_id           uuid        references characters (id) on delete set null,
    product_id             uuid        references products (id) on delete set null,
    requested_by           text,
    status                 text        not null default 'draft'
                           check (status in ('draft','script_review','generating',
                                             'video_review','approved','published',
                                             'cancelled','failed')),
    current_script_version int,
    current_video_version  int,
    publish_on             date,
    caption                text,
    hashtags               text[]      not null default '{}',
    target_platforms       text[],
    spend_cents            int         not null default 0,
    created_at             timestamptz not null default now(),
    approved_at            timestamptz,
    published_at           timestamptz
);
create index if not exists idx_videos_status on videos (status);
create index if not exists idx_videos_publish_on on videos (publish_on) where publish_on is not null;

-- ---------------------------------------------------------------------------
-- video_script_versions — every screenplay revision, immutable.
-- ---------------------------------------------------------------------------
create table if not exists video_script_versions (
    id            uuid primary key default gen_random_uuid(),
    video_id      uuid        not null references videos (id) on delete cascade,
    version       int         not null,
    script        jsonb       not null,
    source        text        not null default 'initial'
                  check (source in ('initial','revision')),
    feedback_text text,
    created_at    timestamptz not null default now(),
    unique (video_id, version)
);

-- ---------------------------------------------------------------------------
-- video_scenes — per-scene artifacts. content_hash is the never-pay-twice key:
-- a scene is regenerated only when its (script, keyframe, audio) inputs change.
-- ---------------------------------------------------------------------------
create table if not exists video_scenes (
    id             uuid primary key default gen_random_uuid(),
    video_id       uuid        not null references videos (id) on delete cascade,
    script_version int         not null,
    idx            int         not null,
    kind           text        not null check (kind in ('speaking','broll')),
    keyframe_url   text,
    audio_url      text,
    clip_url       text,
    content_hash   text,
    provider       text,
    qc             jsonb       not null default '{}'::jsonb,
    spend_cents    int         not null default 0,
    created_at     timestamptz not null default now(),
    unique (video_id, script_version, idx)
);
create index if not exists idx_video_scenes_hash on video_scenes (content_hash);

-- ---------------------------------------------------------------------------
-- video_versions — one row per assembled cut (edit spec + rendered artifacts).
-- parent_version makes "go back to the previous cut" a lookup.
-- ---------------------------------------------------------------------------
create table if not exists video_versions (
    id             uuid primary key default gen_random_uuid(),
    video_id       uuid        not null references videos (id) on delete cascade,
    version        int         not null,
    edit_spec      jsonb       not null,
    master_url     text,
    preview_url    text,
    seconds        numeric,
    parent_version int,
    edit_class     text check (edit_class in ('post','audio','scene','script')),
    feedback_text  text,
    created_at     timestamptz not null default now(),
    unique (video_id, version)
);

-- ---------------------------------------------------------------------------
-- video_jobs — resumable pipeline ledger. A crash resumes from the last
-- completed stage; artifacts + content_hash make every stage idempotent.
-- ---------------------------------------------------------------------------
create table if not exists video_jobs (
    id           uuid primary key default gen_random_uuid(),
    video_id     uuid        not null references videos (id) on delete cascade,
    stage        text        not null
                 check (stage in ('script','keyframes','audio','scenes',
                                  'assemble','preview','publish')),
    status       text        not null default 'pending'
                 check (status in ('pending','running','done','failed','cancelled')),
    attempt      int         not null default 0,
    provider_ref text,
    error        text,
    started_at   timestamptz,
    finished_at  timestamptz,
    created_at   timestamptz not null default now()
);
create index if not exists idx_video_jobs_open on video_jobs (video_id, stage)
    where status in ('pending','running');
