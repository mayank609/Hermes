# Telegram Information Extraction Agent

A production-ready Python service that monitors Telegram groups you're a
member of and turns every new message into structured, searchable
information (jobs, internships, hackathons, AI news, events, funding
announcements, open-source opportunities, research papers, deadlines,
links, and contacts) using the Gemini API.

## How it works

```
Telethon (multi-group listener)
        │  new message
        ▼
MessageProcessor (extractor.py)
        │  dedup check (message id + content hash)
        │  optional keyword pre-filter
        ▼
GeminiExtractor (gemini.py)
        │  structured JSON output, retried with backoff
        ▼
Database (database.py, SQLite via SQLAlchemy)
        │
        ▼
FastAPI REST API (api.py) ── search / stats / daily summary / CSV / Excel
```

- **app/config.py** – loads and validates all configuration from `.env`.
- **app/models.py** – SQLAlchemy tables (`messages`, `extracted_info`) and
  the Pydantic `ExtractionResult` schema Gemini must return.
- **app/telegram_client.py** – Telethon client: resolves the configured
  groups, listens for new messages, auto-reconnects, and handles
  `FloodWaitError` rate limiting.
- **app/gemini.py** – wraps the Gemini API with constrained JSON output
  (`response_schema=ExtractionResult`) and `tenacity` retry/backoff for
  transient failures (429/5xx).
- **app/extractor.py** – orchestration: deduplication, keyword filtering,
  calling Gemini, and persisting results. Framework-agnostic and unit
  tested with a fake Gemini client.
- **app/database.py** – async SQLite (aiosqlite) access layer: dedup
  checks, search/filter, dashboard stats, daily summaries, export queries.
  WAL mode is enabled so the listener and API can share the DB file safely.
- **app/exporter.py** – CSV / Excel serialization used by the API.
- **app/api.py** – optional FastAPI app exposing search, stats, daily
  summary, and export endpoints.
- **app/main.py** – entrypoint; runs the Telegram listener, the API
  server, and a daily-summary scheduler concurrently under one event loop,
  with graceful shutdown on SIGINT/SIGTERM.

## Requirements

- Python 3.11+
- A Telegram account that has already joined the groups you want to
  monitor (Telethon uses your personal account, not a bot, so it can read
  groups you're not an admin of).
- Telegram API credentials from <https://my.telegram.org/apps>.
- A Gemini API key from <https://aistudio.google.com/apikey>.

## Setup

```bash
git clone <this-repo>
cd Hermes
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# now edit .env and fill in API_ID, API_HASH, PHONE_NUMBER, GEMINI_API_KEY,
# and TELEGRAM_GROUPS (comma-separated usernames or numeric chat IDs)
```

### Configure `.env`

| Variable | Required | Description |
|---|---|---|
| `API_ID`, `API_HASH` | yes | From <https://my.telegram.org/apps> |
| `PHONE_NUMBER` | yes | Your Telegram account phone number, e.g. `+15551234567` |
| `TELEGRAM_GROUPS` | yes | Comma-separated group usernames (`@group`) or numeric chat IDs |
| `GEMINI_API_KEY` | yes | From Google AI Studio |
| `GEMINI_MODEL` | no | Defaults to `gemini-2.5-flash` |
| `KEYWORDS` | no | Comma-separated keywords; if set, only matching messages are sent to Gemini |
| `DATABASE_URL` | no | Defaults to local SQLite; see "Upgrading to PostgreSQL" below |
| `RUN_API` | no | `true`/`false`, defaults to `true` |
| `DAILY_SUMMARY_HOUR` | no | UTC hour (0-23) to generate the daily digest |

### First run (interactive Telegram login)

The first time you run the agent, Telethon needs an interactive terminal
to prompt for the login code Telegram sends you (and your 2FA password, if
enabled):

```bash
python -m app.main
```

This creates `data/<SESSION_NAME>.session`, which is reused on every
subsequent run — no more prompts after that. Keep this file private; it's
equivalent to being logged into your Telegram account (it's already
excluded via `.gitignore`).

The process then runs forever: listening to your configured groups, and
(if `RUN_API=true`) serving the REST API on `http://localhost:8000`.

## Gemini extraction schema

Every message is sent to Gemini with a system prompt instructing it to
return **only** JSON matching this schema:

```json
{
  "category": "Job | Internship | Hackathon | AI News | Event | Funding | Open Source | Research Paper | Deadline | Important Link | Contact | Other",
  "title": "",
  "summary": "",
  "company": "",
  "role": "",
  "location": "",
  "deadline": "",
  "apply_link": "",
  "contact": "",
  "important_links": [],
  "skills": [],
  "tags": [],
  "is_useful": true
}
```

Irrelevant messages (small talk, greetings, spam) return `{"is_useful": false}`.
Structured output is enforced via the Gemini SDK's `response_schema`
(bound to a Pydantic model), so the response is validated JSON, not
free-form text that needs regex parsing.

## Database

Tables are created automatically on startup (no manual migrations):

- **messages** — `message_id`, `group_id`, `group_name`, `sender_id`,
  `sender_name`, `raw_text`, `content_hash`, `message_date`, `created_at`.
  Unique on `(group_id, message_id)` for duplicate detection.
- **extracted_info** — every field of the Gemini schema, plus a foreign
  key back to `messages` and `created_at`.

### Upgrading to PostgreSQL

Install `asyncpg` and change `DATABASE_URL`:

```
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/telegram_agent
```

No code changes are required — the schema is defined with SQLAlchemy and
is portable across both backends.

## REST API

With `RUN_API=true`, the following endpoints are available (interactive
docs at `/docs`):

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness check |
| `GET /messages?q=&category=&group_name=&is_useful=&start_date=&end_date=&limit=&offset=` | Search / filter extracted items |
| `GET /messages/{id}` | Fetch a single extraction |
| `GET /stats` | Dashboard-ready counts by category and by group |
| `GET /summary/daily?date=YYYY-MM-DD` | Daily digest (defaults to today, UTC) |
| `GET /export/csv?category=&group_name=&is_useful=` | Export filtered results as CSV |
| `GET /export/excel?category=&group_name=&is_useful=` | Export filtered results as `.xlsx` |

## Features implemented

- Async, non-blocking pipeline end to end (Telethon + Gemini SDK async
  client + SQLAlchemy async + FastAPI).
- Multiple Telegram groups monitored concurrently from one client.
- Automatic reconnect (`auto_reconnect=True`, infinite `connection_retries`).
- Telegram flood-wait handling (`FloodWaitError` caught and slept out).
- Gemini retry logic with exponential backoff (`tenacity`), retrying only
  on transient errors (429/5xx/timeouts), failing fast on bad requests.
- Structured logging to console + rotating file (`data/logs/agent.log`).
- Per-message error isolation — one bad message or failed extraction never
  crashes the listener; it's stored with `extraction_failed=true` instead.
- Environment-variable-driven configuration with clear startup errors for
  missing values.
- Keyword pre-filtering to cut Gemini API cost.
- Duplicate detection both by `(group, message_id)` and by normalized
  content hash (catches the same announcement cross-posted to multiple
  groups).
- Search API, CSV/Excel export, dashboard stats, daily summary generation
  (logged and written to `data/summaries/YYYY-MM-DD.json`).
- Docker / docker-compose support.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a fake Gemini client and a temporary SQLite database, so they
run fully offline — no API keys or Telegram login required.

## Docker

```bash
cp .env.example .env   # fill in your values first

# First run needs an interactive terminal for the Telegram login prompt:
docker compose run --rm agent

# Subsequent runs reuse the saved session:
docker compose up -d
```

The `./data` directory (database, logs, exports, summaries, and the
Telethon session file) is bind-mounted into the container so it persists
across restarts.

## Project layout

```
Hermes/
├── app/
│   ├── telegram_client.py   # Telethon listener
│   ├── gemini.py            # Gemini API wrapper + retry logic
│   ├── extractor.py         # dedup + filter + orchestration pipeline
│   ├── database.py          # async SQLite access layer
│   ├── models.py            # SQLAlchemy tables + Pydantic schemas
│   ├── config.py            # environment-driven settings
│   ├── exporter.py          # CSV / Excel serialization
│   ├── api.py                # FastAPI REST API
│   ├── logging_config.py    # console + rotating file logging
│   └── main.py               # entrypoint wiring everything together
├── tests/                    # offline unit + API tests
├── data/                     # SQLite DB, logs, exports, session file (gitignored)
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
