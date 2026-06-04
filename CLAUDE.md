# Globex Social Media Automation System

## What This Is
AI-powered social media automation for Globex International, a global food trading company (90+ countries, 300+ suppliers, 950+ trade partners). Replaces their $45K/year social media manager with an AI system Karen Joseph can control from her phone.

**Tier 2 build** — WhatsApp chat command center with automated content and approval workflow.

## Active work
See [globex-sm-automation-plan.md](globex-sm-automation-plan.md) — full phased build plan with checkboxes and progress log.

## Architecture

```
Karen sends WhatsApp message
  → Twilio webhook → FastAPI backend
  → Parse intent (new post / approval / edit)
  → Claude API generates copy + selects template
  → Puppeteer renders branded image from HTML/CSS template
  → Send preview back via WhatsApp
  → Karen replies "approve" or gives edit feedback
  → If approved → Blotato API publishes to Instagram + Facebook + LinkedIn
  → Log to database
```

Automated flow (daily scheduler):
```
APScheduler checks upcoming holidays/anniversaries/trade shows
  → Generates posts for events in next 3-7 days
  → Sends to Karen via WhatsApp for approval
  → Same approval flow as above
```

## Tech Stack
- **Backend:** Python 3.12+ with FastAPI
- **AI:** Anthropic Claude API (claude-opus-4-7) — content generation, tone matching, intent parsing. Use Opus for everything.
- **Messaging:** Twilio WhatsApp Business API — webhook-based, receives messages + photos, sends previews
- **Publishing:** Blotato API — posts to Instagram, Facebook, LinkedIn
- **Image generation:** Puppeteer (Node) or Playwright — renders HTML/CSS branded templates to PNG
- **Database:** Supabase (PostgreSQL) — stores employee data, holidays, trade shows, post history, approval status. Use supabase-py client.
- **Scheduling:** APScheduler — daily content generation for upcoming events
- **Hosting:** Railway (Pro plan, $20/mo)

## Project Structure
```
globex-social/
├── CLAUDE.md
├── app/
│   ├── main.py              # FastAPI app, Twilio webhook endpoints
│   ├── config.py             # Env vars, API keys, constants
│   ├── ai/
│   │   ├── generator.py      # Claude API calls, content generation
│   │   └── prompts.py        # System prompts, brand voice, content rules
│   ├── messaging/
│   │   ├── whatsapp.py       # Twilio send/receive, message parsing
│   │   └── intent.py         # Parse Karen's messages into actions
│   ├── publishing/
│   │   └── blotato.py        # Blotato API integration, multi-platform posting
│   ├── templates/
│   │   ├── renderer.py       # HTML-to-PNG rendering via Puppeteer/Playwright
│   │   └── html/             # Branded HTML/CSS post templates
│   ├── scheduler/
│   │   └── automation.py     # APScheduler jobs, holiday/anniversary checks
│   ├── db/
│   │   ├── models.py         # Supabase table schemas and query helpers
│   │   └── supabase.py       # Supabase client init, connection config
│   └── data/
│       ├── employees.json    # Employee list with hire dates (from Karen's Excel)
│       ├── holidays.json     # Holiday calendar
│       └── trade_shows.json  # Trade show schedule
├── templates/                # HTML/CSS branded post templates
├── tests/
├── requirements.txt
└── Procfile                  # Railway deployment
```

## Brand Rules — IMPORTANT
- **Colors:** Pantone 288C (#002D72, deep navy) and Pantone 2985C (#5BC2E7, cyan). These are the ONLY brand colors. No other colors in templates.
- **Tone:** Professional but human. Not corporate-stuffy. Karen's voice is direct, confident, no-nonsense.
- **Logo:** Must appear on every generated post. Use transparent PNG version.
- **Content Karen does NOT want:** employee birthdays, weekly employee features, kitschy posts, oversaturated posting. She removed these explicitly.
- **Content Karen DOES want:** trade show coverage, major milestones (20+ year anniversaries only), industry holidays (National Poultry Day, Chinese New Year, Ramadan, Super Bowl), quarterly recipe posts (Alan's hot sauces), shipment/impact stats, industry news.
- **Len (owner) is a control freak.** Every post goes through approval. NOTHING auto-publishes without Karen confirming.

## Commands
```bash
# Run the server locally
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v

# Type check
mypy app/ --ignore-missing-imports

# Format
ruff format app/ tests/
ruff check app/ tests/ --fix

# Start Puppeteer renderer (separate process)
node templates/render-server.js

# Expose local server for Twilio webhook testing
ngrok http 8000
```

## Key Technical Decisions
- **Puppeteer for images, not AI image generation** — because brand consistency matters more than creativity. HTML/CSS templates guarantee pixel-perfect output every time. Karen's standard is high; inconsistent AI images would lose trust.
- **Supabase not raw Postgres** — gives us managed PostgreSQL with a Python client, built-in auth if needed for Tier 3 upgrade, and a dashboard Karen's team can peek at. Use supabase-py for all DB operations, not raw SQL.
- **APScheduler inside FastAPI, not cron** — keeps everything in one process on Railway. No separate worker needed.
- **Blotato for publishing, not direct API calls** — handles OAuth token refresh, rate limits, and format differences across platforms. One integration instead of three.
- **Twilio not Meta Cloud API** — faster setup, better docs, handles WhatsApp Business API approval. More expensive per-message but worth the dev time saved.

## Environment Variables
```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=          # Whisper transcription of WhatsApp voice notes only (optional)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=
AUTHORIZED_NUMBERS=
BLOTATO_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
ENVIRONMENT=development|production
```

## Anti-Patterns — DO NOT
- **Never auto-publish without approval.** Every post must go through the WhatsApp approval flow. This is a contract requirement, not a preference.
- **Never use colors outside the brand palette** in templates. Karen will reject anything off-brand immediately.
- **Never generate employee birthday posts.** Karen explicitly killed these. Don't resurface them.
- **Never hardcode authorized phone numbers** in source code. `AUTHORIZED_NUMBERS` is a comma-separated allowlist (`whatsapp:+19178592787,whatsapp:+...`). Karen's number is the primary entry; dev/test numbers can be added so the developer can debug against the production bot without disrupting Karen.
- **Never store API keys in code.** Use .env files locally, Railway env vars in production.
- **Never use `print()` for logging.** Use Python's `logging` module with structured output.

## Approval Flow States
Posts move through: `DRAFT → PENDING_APPROVAL → APPROVED → PUBLISHED` or `DRAFT → PENDING_APPROVAL → EDIT_REQUESTED → DRAFT` (loops back). Track state transitions with timestamps in the database.

## Content Categories
Each maps to an HTML template and a Claude system prompt variant:
1. `trade_show` — pre-event, during-event (with photo), post-event thank-you
2. `holiday` — National Poultry Day, Chinese New Year, Ramadan, Memorial Day, etc.
3. `milestone` — employee anniversaries, 20+ years only
4. `recipe` — quarterly, Alan's hot sauces, formatted for international audience
5. `stats` — shipment volumes, market data, company impact numbers
6. `announcement` — new hires, partnerships, general company news
7. `custom` — Karen sends photo + description, AI creates post from scratch

## Testing
- Test the WhatsApp webhook with Twilio's request validator to verify signatures
- Test image rendering by generating all template types and visually inspecting output
- Test approval flow end-to-end: send message → receive preview → approve → verify publish call
- Mock Blotato API responses in tests; never call production publishing in test mode