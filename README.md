# FreightAI MVP — Telegram Freight Matching Bot

A minimal, secure, concurrency-safe MVP that connects truck owners
looking for loads with load owners looking for carriers, entirely
through a Telegram bot. The platform admin mediates the final contact —
this is **not** an open marketplace where users message each other
directly.

Built fresh for this scope (not a trimmed copy of any larger reference
project). Explicitly out of scope for this MVP: payments, pricing
engine, invoices, documents, dashboard/frontend, ML, and advanced
marketplace features. The codebase is modular so those can be added
later without rewriting the core.

## Architecture

```
Telegram user
     │
     ▼
  bot (aiogram 3, long polling)
     │  HTTP + X-Bot-Secret header
     ▼
  backend (FastAPI)  ──┬── PostgreSQL (source of truth)
     │                 └── Redis (rate limiting, locks, FSM state)
     ▼
  worker (reminders + proactive/backhaul matching, polls every 60s)
```

- **backend/** — FastAPI app: registration, truck/load creation,
  deterministic matching engine, notifications, admin mediation.
- **bot/** — aiogram 3 Telegram bot: conversational flows, free-text
  intake with AI-assisted extraction + mandatory user confirmation,
  manual fallback, admin panel commands.
- **tests/** — pytest suite covering matching, concurrency, security,
  notifications, and registration.

## Quick start

```bash
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID,
# BOT_SERVICE_SECRET (openssl rand -hex 32), and optionally
# ANTHROPIC_API_KEY for free-text extraction.

docker compose up --build
```

Then in Telegram:
1. Message your bot, `/start`.
2. Choose "🚛 أنا صاحب شاحنة" or "📦 أنا صاحب حمولة".
3. Give your name and phone (only ever seen by the admin).
4. Describe your truck/load in free text, or let it fall back to a
   step-by-step manual form if extraction can't parse it confidently.
5. Confirm the extracted summary before anything is saved.

As the admin (the account whose Telegram numeric chat ID matches
`TELEGRAM_ADMIN_CHAT_ID` on its *first* `/start`), run `/admin` to see
open loads, available trucks, and pending matches, with buttons to view
contact info, mark a match connected, or reject it.

To promote a different account to admin later (rather than relying on
the env-var bootstrap), use the operator script — this is the *only*
other way admin is ever granted, there is no self-promotion path via
the bot or API:

```bash
docker compose exec backend python -m scripts.promote_admin <telegram_id>
```

## Core workflow (scenario 1 — carrier registers, load already waiting)

```
شاحنتي في الدمام وأبي حمولة لجدة
  → extraction → confirmation → saved
  → matching engine checks WAITING_FOR_MATCH loads
  → match found → notify carrier, shipper, admin (no phone numbers exchanged)
  → admin runs /admin → 📞 تواصل → connects both parties
```

## Core workflow (scenario 2 — load registers, no carrier yet)

```
عندي 3 تريلات من الدمام لجدة
  → no matching truck right now
  → load = WAITING_FOR_MATCH, shipper told "still searching"
  → filtered broadcast to eligible carriers with an "🚛 أنا مهتم" button
  → 10 minutes later: one reminder to carriers who haven't responded
  → carrier presses "interested" → admin + shipper notified → admin connects
```

## Core workflow (scenario 3 — proactive/backhaul)

```
Carrier reports an active trip: Dammam → Jeddah, ETA in 8h
  → truck.status = ON_TRIP, trip_destination = جدة, trip_eta = now+8h
  → background worker scans ON_TRIP trucks within ±60 min of ETA
  → finds a WAITING_FOR_MATCH load originating from جدة
  → proactive match created, shipper + carrier + admin notified
```

## Trusted pairs (repeat partners skip the manual admin step)

Requiring the admin to manually approve every single match — even
between two people who've already worked together before and already
have each other's phone number — is pure friction with no privacy
benefit. So:

- The **first** time a shipper and a carrier are matched, it always
  goes through the normal flow: `PENDING` match, no contact info
  shared, admin reviews via `/admin` and presses `✅ تم الربط`.
- That action records a `TrustedPair` for that specific shipper +
  carrier combination (`backend/app/services/trusted_pairs.py`).
- Any **future** match between that exact same pair is auto-connected
  immediately — `CONNECTED` status, both parties notified directly with
  each other's name and phone. This is the one deliberate exception to
  "never reveal contact info automatically" — and it's safe precisely
  *because* it isn't new exposure: they already exchanged that info
  once, under admin supervision.
- Trust is scoped to the pair, never generalized — a carrier trusted
  with one shipper is not auto-connected with a different shipper.
- Proactive/backhaul matches and interest-based matches (broadcast →
  "🚛 أنا مهتم") intentionally still go through admin review even for
  trusted pairs, since those involve more uncertainty (approximate ETA
  windows, unsolicited interest) than a direct route match — this can
  be extended later if desired.

See `tests/test_trusted_pairs.py`.

## Free single-service hosting (no VPS, no credit card charges)

The default `docker compose up --build` setup above needs an always-on
host (a VPS, Oracle Cloud Always Free, etc.) because it runs 3 long-lived
processes: `backend`, `bot` (long-polling), and `worker` (a loop that
never exits). Most genuinely free hosts (Render's free tier, etc.) only
offer a **request-driven web service** that sleeps between requests and
give **no free background-worker tier** — a separate always-running
`bot`/`worker` simply isn't possible there.

`Dockerfile.webapp` collapses everything into **one** request-driven web
service instead, so it fits that model:

| Piece | Normal (docker-compose) | Free single-service mode |
|---|---|---|
| Bot transport | long polling (own process) | Telegram **webhook** → `POST /telegram/webhook`, handled by the same FastAPI app |
| Reminders / proactive matching | standalone `worker` loop | external free pinger calls `POST /internal/cron/tick` every few minutes |
| What keeps it "alive" | nothing needed, it's a VM | the same ping that runs the cron tick also keeps the service warm |

Nothing in the matching/notification/security logic changes — same
code, same database, same tests. Only the transport for the bot and the
trigger for the periodic jobs differ, both controlled by `BOT_MODE` (see
`.env.example`).

### 1. Get free managed Postgres + Redis

Render/Fly's free web-service tier doesn't include a persistent database
either, so use separate free managed services instead:
- **Postgres**: [Supabase](https://supabase.com) or [Neon](https://neon.tech) free tier — copy the `postgresql://` connection string, and change its scheme to `postgresql+asyncpg://` for `DATABASE_URL`.
- **Redis**: [Upstash](https://upstash.com) free tier (serverless Redis, works over TLS) — copy the `rediss://` connection string for `REDIS_URL`.

### 2. Deploy `Dockerfile.webapp`

On your host of choice (Render, Fly.io, etc.), create a new web service
pointing at this repo, telling it to build `Dockerfile.webapp` at the
repo root (not `backend/Dockerfile`). Set these environment variables
(see `.env.example` for the full annotated list):

```
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://...        # from Supabase/Neon
REDIS_URL=rediss://...                        # from Upstash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
BOT_SERVICE_SECRET=<openssl rand -hex 32>
BACKEND_BASE_URL=http://127.0.0.1:8000        # bot talks to itself, same process
BOT_MODE=webhook
PUBLIC_BASE_URL=https://<your-service>.onrender.com   # your host assigns this
TELEGRAM_WEBHOOK_SECRET=<openssl rand -hex 32>
CRON_SECRET=<openssl rand -hex 32>
```

On startup, the app registers itself with Telegram as the webhook target
automatically (see `app/main.py`) — no manual `setWebhook` call needed.

### 3. Point a free external pinger at `/internal/cron/tick`

Use a free scheduled-HTTP service such as [cron-job.org](https://cron-job.org)
to send this every 5 minutes:

```
POST https://<your-service>.onrender.com/internal/cron/tick
Header: X-Cron-Secret: <the CRON_SECRET you set above>
```

This single ping does double duty: it drives the reminder + proactive-
matching pass (see `app/api/cron.py`), **and** its own HTTP request is
what keeps a sleep-after-idle host warm — no separate "keep-alive" ping
needed on top of it.

### Trade-offs of this mode, honestly

- A **cold start** after idle time can delay the very first reply by a
  few seconds (the underlying limitation of a free tier that sleeps —
  no configuration fully eliminates this, only masks it).
- Free managed Postgres/Redis tiers usually have connection/storage caps
  fine for an MVP's traffic, but check current limits on Supabase/Neon/
  Upstash before relying on this for anything beyond testing.
- This is a genuinely good fit for demoing, testing, or low-volume real
  use. For production traffic, prefer the docker-compose + VPS path.

## Matching rules

Origin and destination match (after city-spelling normalization) is
**mandatory** — a load Dammam→Jeddah never matches a truck
Riyadh→Jeddah, regardless of score. Optional signals (truck type,
count, availability) only adjust a 0–100 score on top of that. Matching
is 100% rule-based in the backend; AI is never involved in the matching
or authorization decision (see `backend/app/services/matching.py`).

## Security highlights

See `SECURITY.md` for the full list. Summary:
- Every backend `/internal/*` route requires a shared `X-Bot-Secret`
  header known only to the bot process — the API is not exposed to
  arbitrary clients.
- Admin status lives in `users.role` in the database, never in a
  Telegram username, and is only ever set by the one-time bootstrap
  (matching `TELEGRAM_ADMIN_CHAT_ID` on first `/start`) or the
  `scripts/promote_admin.py` operator script — no API path lets a user
  self-promote.
- Ownership is checked server-side on every mutation (e.g. registering
  interest requires the caller's own truck).
- Phone numbers are never sent to either party automatically — only the
  admin ever sees them, through an audited `contact` action.
- AI is used strictly for text → structured-JSON extraction, output is
  re-validated against a strict Pydantic schema, and it never touches
  the database, sends messages, or makes an authorization/matching
  decision.
- Concurrent match/notification creation is race-safe via database
  unique constraints, not in-memory checks (see `tests/test_concurrency.py`).
- Rate limits (messages/min, loads/hour, trucks/hour, AI calls/hour)
  are enforced server-side, keyed by `telegram_id`.

## Running tests

```bash
cd backend && pip install -r requirements-dev.txt
cd .. && pytest tests -v
```

Tests use an in-memory SQLite database (same SQLAlchemy models,
same unique constraints) so they run without needing Postgres/Redis —
see `tests/conftest.py`.

## Environment variables

See `.env.example` for the full list with comments. Never commit `.env`.

## Project layout

```
freightai/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/           config, database, redis, logging, auth
│   │   ├── models/         SQLAlchemy models
│   │   ├── schemas/        Pydantic request/response + AI-output schema
│   │   ├── services/       matching, notifications, extraction,
│   │   │                   proactive matching, rate limiting, audit,
│   │   │                   telegram client, city normalization
│   │   ├── api/            users, trucks, loads, interests, admin,
│   │   │                   extract, health
│   │   └── workers/        reminder + proactive-matching scheduler
│   ├── alembic/            migration scaffolding (MVP auto-creates
│   │                       tables on startup; switch to alembic for prod)
│   ├── scripts/            promote_admin.py operator script
│   └── requirements*.txt, Dockerfile
├── bot/
│   ├── bot/
│   │   ├── main.py
│   │   ├── handlers/       start, intake (free-text + manual fallback),
│   │   │                   admin
│   │   ├── api_client.py, keyboards.py, states.py, middlewares.py
│   └── requirements.txt, Dockerfile
├── tests/
├── docker-compose.yml
├── .env.example
├── README.md
└── SECURITY.md
```
