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
