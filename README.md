# Globex Social Media Automation

AI-powered WhatsApp command center that lets Karen Joseph run Globex International's
social media (Instagram, Facebook, LinkedIn) from her phone. She messages a request,
gets a brand-perfect preview back, approves it, and it publishes — nothing goes live
without her explicit approval.

See [`CLAUDE.md`](CLAUDE.md) for brand rules and [`globex-sm-automation-plan.md`](globex-sm-automation-plan.md)
for the full phased build plan.

## Stack
FastAPI · Anthropic Claude · Twilio WhatsApp · Playwright (HTML→PNG) · Supabase
(Postgres + Storage) · Blotato (publishing) · APScheduler · Railway.

## Local setup

### 1. System tools (for Phase 0 asset import only)
EPS→PNG logo conversion needs ImageMagick + Ghostscript. On Windows via Scoop:
```powershell
scoop install imagemagick ghostscript
```

### 2. Python environment
Python 3.12+.
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # dev (tests, lint)
# Production/Railway installs runtime only:  pip install -r requirements.txt
```

### 3. Configuration
```powershell
Copy-Item .env.example .env
```
Fill `.env` with the real credentials (never commit it — it's gitignored):
- `ANTHROPIC_API_KEY`, `TWILIO_*`, `AUTHORIZED_NUMBERS`, `BLOTATO_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY` (service_role) — used by all runtime db helpers
- `SUPABASE_DB_URL` — Postgres URI for schema DDL only. Supabase dashboard →
  Settings → Database → Connection string → URI (Session pooler).

### 4. Database
```powershell
.\.venv\Scripts\python.exe scripts\apply_schema.py   # create tables + storage bucket (idempotent)
.\.venv\Scripts\python.exe scripts\seed_db.py        # load employees/holidays/trade_shows (idempotent)
```

### 5. Run
```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000
# Health check:
curl http://localhost:8000/health
```
`/health` returns 200 with `{"status":"ok","supabase":"connected","anthropic":"configured"}`
when everything is wired up.

## Quality gates
```powershell
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m mypy app scripts
.\.venv\Scripts\python.exe -m pytest -v
```
DB helper tests are integration tests against the dev Supabase project; they skip
automatically if it isn't configured or the schema hasn't been applied.

## Phase 0 asset import (one-off)
```powershell
.\.venv\Scripts\python.exe scripts\import_assets.py
```
Converts the EPS logos, parses the Globex Excel into `app/data/*.json`, and writes
`docs/missing_assets.md`. See that file for outstanding assets needed from the client.

## Layout
```
app/        FastAPI app, config, logging, db helpers, (later) ai/messaging/publishing/templates/scheduler
scripts/    one-off + ops scripts (import_assets, apply_schema, seed_db)
tests/      pytest suite + fixtures
docs/       runbook, gap report, reference material
```
