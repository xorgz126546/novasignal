# Installation (local development)

## Requirements

* Python 3.11+ (tested with 3.13)
* Node.js 20+ (tested with 22)
* Optional: PostgreSQL 16 (SQLite is used automatically when `DATABASE_URL` is not set)
* Network access to `data-api.binance.vision` (the public market-data host).
  `api.binance.com` returns **HTTP 451** from restricted regions, including some US cloud hosts. The default host avoids that.

## Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp ../.env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY
python -m pytest -q
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/api/docs (disabled when `ENVIRONMENT=production`).

Run tests against PostgreSQL:

```bash
createdb cryptonex_test
TEST_DATABASE_URL="postgresql+psycopg://USER:PASS@localhost:5432/cryptonex_test" python -m pytest -q
```

## Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api → http://localhost:8000
npm run typecheck && npm test && npm run build
```

## First run

1. Open http://localhost:5173 and register. **The first account becomes admin.** You can also set
   `BOOTSTRAP_ADMIN_EMAIL/PASSWORD` to create the admin at startup.
2. Admin → **Run scan now**. This runs a full live cycle on Binance public data. Zero approvals is normal.
3. To scan automatically, set `ENABLE_SCHEDULER=true` and restart. This gives a scan every 15 min, the tracker every 60 s, and a daily summary at 21:00 UTC.
4. Telegram (optional for local use): put `TELEGRAM_BOT_TOKEN` in `.env`, run `python -m app.telegram.poll`
   (polling mode, no public URL needed), then Settings → Telegram → Generate link code → send `/start CODE` to your bot.

## Full stack with Docker (PostgreSQL + API + web)

```bash
cp .env.example .env     # set SECRET_KEY, POSTGRES_PASSWORD, TELEGRAM_* ...
docker compose up --build
# open http://localhost:8080
```

> The Docker files were written to standard patterns but could **not** be build-tested in the development
> container (no Docker daemon). Run `docker compose up --build` once and report any issue.
