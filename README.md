# CRYPTONEX AI — Intelligent Crypto Signals

An AI-assisted market-analysis and signal platform for **Binance Spot**. Eleven specialized agents
evaluate the market, and a Chief Trading Officer agent publishes **at most 3 BUY signals per day**.
Each signal has one entry, **one take profit and one stop loss**. On many days it publishes none.

> **No guarantees.** CRYPTONEX AI never promises profits, win rates or daily opportunities. It never
> executes trades or touches withdrawals. You decide and execute every trade manually on Binance.

This is an independent project with no connection to any other company.

## What's in the box

| Area | Where |
|---|---|
| FastAPI backend: 11 agents, pipeline, tracker, paper trading, reports, admin, auth | `backend/app` |
| Binance public market-data client (no keys, rate-limited, validated, stale-data checks) | `backend/app/market/binance.py` |
| Deterministic indicators (RSI, MACD, EMA 9/21/50/200, Bollinger, ATR, ADX) | `backend/app/market/indicators.py` |
| The 11 agents (each with input/output schema, validation, logging, error handling, tests) | `backend/app/agents/` |
| Telegram bot (webhook with secret validation or polling; ENTERED/SKIPPED/CLOSED flows) | `backend/app/telegram/` |
| React + TypeScript + Tailwind + Recharts dashboard, landing page, admin panel | `frontend/` |
| Tests (71 backend, passing on SQLite **and** PostgreSQL 16; frontend unit tests) | `backend/tests`, `frontend/src/**/*.test.ts` |
| Docs: architecture, agents, confidence model, install, deploy, security, validation plan, 30-day report template | `docs/` |
| Environment template (placeholders only) | `.env.example` |

## Quick start (local)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env            # edit SECRET_KEY at minimum
python -m pytest -q                # 71 tests
uvicorn app.main:app --reload      # http://localhost:8000/api/docs

# Frontend (second terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173  (proxies /api to :8000)
```

Register the first account; it becomes the **admin**. Open **Admin → Run scan now** to run a full live cycle
against Binance public data. To scan automatically, set `ENABLE_SCHEDULER=true`. To use Telegram, see
`docs/DEPLOYMENT.md#telegram`.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): system design, data model, signal lifecycle, the three performance tracks
- [Agents](docs/AGENTS.md): what each of the 11 agents does, its inputs/outputs and its rules
- [Confidence model](docs/CONFIDENCE_MODEL.md): how the 0–100 score is computed (and why it is not a probability)
- [Installation](docs/INSTALLATION.md) · [Deployment](docs/DEPLOYMENT.md) · [Security](docs/SECURITY.md)
- [30-day validation plan](docs/VALIDATION_PLAN.md) · [Report template](docs/30_DAY_REPORT_TEMPLATE.md)
- [Project status & handover](docs/STATUS.md): what works, what still needs credentials, test results
- [Available tools & skills](AVAILABLE_TOOLS_AND_SKILLS.md) · [Connectors & skills usage report](CONNECTORS_AND_SKILLS_USAGE_REPORT.md)

## Legacy code

The original prototype scripts are in `legacy/` for reference only. **The Telegram bot token that was
hard-coded in the original `bot.py` is still present in git history and must be revoked with @BotFather.**
The working-tree copy now reads the token from an environment variable.
