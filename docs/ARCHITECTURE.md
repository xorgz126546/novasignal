# Architecture

## Overview

```
                 ┌────────────── Binance Spot public market data (data-api.binance.vision) ─────────────┐
                 │  exchangeInfo · 24h tickers · klines (15m/1h/4h/1d/1m) · bookTicker · price          │
                 └───────────────▲──────────────────────────────────────────────▲──────────────────────┘
                                 │ rate-limited, validated, stale-checked       │ 1m candles
┌────────────────────────────────┴───────────┐                ┌─────────────────┴──────────┐
│ Scan cycle (every 15 min, scheduler)        │                │ Tracker (every 60 s)       │
│ 01 Scanner → shortlist                      │                │ TP / SL / expiry on 1m     │
│ per candidate: 02 03 04 05 06 07 (analysis) │                │ → theoretical outcome      │
│ 10 Entry/Exit → 08 Risk → 09 Validator      │                │ → paper trade close        │
│ 11 Chief Trading Officer → approve/reject   │                │ → Telegram outcome alert   │
└───────────────┬─────────────────────────────┘                └─────────────┬──────────────┘
                │ Signal, Opportunity, AgentRun, PaperTrade, AuditLog        │
                ▼                                                            ▼
        ┌────────────────────────────── PostgreSQL (single source of truth) ───────────────────────────┐
        └──────────────▲───────────────────────────────▲────────────────────────────────▲──────────────┘
                       │ REST /api (JWT, RBAC)          │ webhook (secret header)         │
                ┌──────┴──────────┐             ┌───────┴────────┐                ┌───────┴────────┐
                │ React dashboard │             │ Telegram bot   │                │ Admin panel    │
                └─────────────────┘             └────────────────┘                └────────────────┘
```

## Technology choices

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12+, FastAPI, async httpx, SQLAlchemy 2 | Required stack; async I/O for market data; typed schemas via pydantic |
| Database | **PostgreSQL** in production, SQLite for dev/tests (one ORM, same schema) | One consistent store: no duplication across Firebase and Postgres |
| Auth | Built-in email/password, bcrypt, JWT (HS256), role-based access (user/admin) | Keeps users, subscriptions, trades and audit in the same transactional DB. Firebase Auth was not used, to avoid a second identity store (see below) |
| Frontend | React 18 + TypeScript + Vite + Tailwind + Recharts | Required stack |
| Hosting | Frontend: Firebase Hosting or any static host. Backend: any container host (Cloud Run, Fly.io, Render, VPS) | `firebase.json` and Dockerfiles included |
| Scheduler | In-process asyncio loops (scan, tracker, daily summary) | Enable on exactly one instance |
| LLM | Optional Claude (`anthropic` SDK) for a plain-English narrative only | Numbers are always deterministic |

**Why not Firebase Auth + Firestore?** The prompt asks for *one* consistent database. Trades, signals,
subscriptions and audit logs are relational and need transactions, so PostgreSQL is the natural fit.
Adding Firebase Auth would mean a second user store to keep in sync. If Firebase Auth is wanted later,
`security.get_current_user` is the single seam where a Firebase ID-token verifier would replace JWT decoding.

## Backend modules

```
backend/app
├── main.py               app factory, CORS, security headers, global rate limit, lifespan
├── config.py             environment-only configuration (pydantic-settings)
├── db.py / models.py     SQLAlchemy engine + schema (timezone-aware UTC everywhere)
├── security.py           bcrypt, JWT, RBAC deps, subscription gate, rate limiter, audit()
├── trading_config.py     runtime trading/risk/indicator settings (DB-stored, validated, audited)
├── market/               binance.py (client) · indicators.py · structure.py (swings, patterns)
├── agents/               base.py (contract) + 11 agents
├── engine/               pipeline · lifecycle · tracker · performance · tracks · reports · services
├── telegram/             client · bot (updates + notifications) · messages · poll (dev)
├── llm/narrative.py      optional explanation text
├── scheduler.py          asyncio jobs
└── api/                  auth · signals/trades · dashboard (overview, agents, analytics) · settings · validation · admin · telegram · public
```

## Data model (main tables)

| Table | Purpose |
|---|---|
| `users`, `subscriptions`, `billing_records` | accounts, roles, Telegram link, subscription state, billing history |
| `scan_cycles` | each pipeline run: status, candidates, approved/rejected, error |
| `opportunities` | **every** evaluated candidate (approved or rejected) and its rejection reasons |
| `agent_runs` | every agent execution: input symbol, pass/score, full output JSON, duration, error |
| `signals` | published signals + levels + confidence breakdown + justification + **theoretical outcome** |
| `signal_events` | full status history of each signal |
| `paper_trades` | simulated fills (fees, slippage, delay) — **paper track** |
| `user_trades` | user-reported ENTERED/SKIPPED/CLOSED with real prices and fees — **actual track** |
| `audit_logs` | security and admin actions |
| `app_settings` | trading config (paused flag, risk, indicators) |
| `validation_programs` | 30-day program windows |
| `telegram_sessions` | multi-step Telegram conversation state |
| `service_states` | health of scanner / tracker / Telegram / scheduler |

## Signal lifecycle

```
ANALYZING → APPROVED → ACTIVE ──► TARGET_HIT | STOP_LOSS_HIT | EXPIRED      (market outcome, 1-minute candles)
                         │  ├──► USER_ENTERED ──► TARGET_HIT | STOP_LOSS_HIT | EXPIRED | CLOSED_MANUALLY
                         │  └──► USER_SKIPPED ──► TARGET_HIT | STOP_LOSS_HIT | EXPIRED
                         └────► CANCELLED (admin)
```

* A signal **never** implies a user trade. Each user's decision is stored on their own `user_trades` row.
* Global `USER_ENTERED / USER_SKIPPED / CLOSED_MANUALLY` statuses reflect the **operator's (admin's)** reports
  during the validation phase. In multi-user mode, each user sees their own decision (`my_decision`).
* Outcome evaluation is conservative: if one 1-minute candle touches both TP and SL, the **stop is assumed first**.
  Expiry is only declared once candle data actually covers the expiry time.

## Three performance tracks (never merged)

| Track | Source | Costs |
|---|---|---|
| Theoretical | signal levels vs 1m candles | none (optimistic upper bound) |
| Paper | simulated fill at a fresh price after approval + slippage; TP exits at limit, SL exits with 2× slippage | 0.10% fee per side (configurable) |
| Actual | the user's reported fills and fees | real |

Every API, report and dashboard view takes **one** track. `/api/analytics?track=mixed` is rejected.

## Development responsibilities

The prompt's 11 engineering roles were covered as follows (by one engineer working sequentially; no parallel
sub-agents were needed):

| Role | Deliverable |
|---|---|
| Software Architect | this document, module boundaries, single-DB decision |
| Backend Engineer | FastAPI app, API routers, scheduler |
| Frontend Engineer / UI-UX Designer | `frontend/` dashboard, landing page, dark theme, responsive layout |
| Quantitative Research Engineer | indicators, agents 02–06/09/10, setup replay, performance metrics, verdict rules |
| AI Agent Engineer | agent contract (`agents/base.py`), Chief Trading Officer, optional LLM narrative |
| Database Engineer | schema, timezone-safe types, SQLite + PostgreSQL verification |
| Security Engineer | auth, RBAC, rate limits, webhook secret, secrets hygiene, `docs/SECURITY.md` |
| QA & Testing Engineer | 70 backend tests + frontend tests + browser E2E run |
| DevOps Engineer | Dockerfiles, docker-compose, CI workflow, Firebase Hosting config |
| Product & Documentation Manager | README, docs/, status report, tool usage reports |
