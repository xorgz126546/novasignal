# Project status & handover

_Last updated: 2026-11-13_

## Completed (all 12 phases implemented; phase 11 is ready to start, phase 12 is prepared but disabled)

| Phase | Status |
|---|---|
| 1 Architecture & repo setup | ✅ monorepo `backend/` + `frontend/` + `docs/`, legacy scripts moved to `legacy/` |
| 2 Database schema & backend | ✅ 14 tables, FastAPI, JWT auth, RBAC, audit log |
| 3 Binance public data | ✅ client with endpoint allow-list, weight budget, retries, 429/418/451 handling, validation, stale-data checks |
| 4 Technical analysis engine | ✅ RSI, MACD, EMA 9/21/50/200, Bollinger, ATR, ADX, swings, levels, candle patterns |
| 5 The 11 agents | ✅ schemas, validation, logging, error capture, per-run persistence, unit tests |
| 6 Signal generation & validation | ✅ pipeline, Chief decisions, documented confidence model, caps, duplicate/cooldown/concurrency protection |
| 7 Web dashboard | ✅ Overview, Live Signals, Signal Details (live chart), Journal, Agent Monitor, Analytics, Validation, Settings, Admin, Landing |
| 8 Telegram bot | ✅ notifications, inline ENTERED/SKIPPED/CLOSED buttons, price/fee capture, /start /dashboard /signals /performance /agents /settings, webhook secret, polling mode |
| 9 Paper trading & tracking | ✅ 1-minute tracker, paper fills with fees/slippage/delay, three separate tracks |
| 10 Testing, security, deployment docs | ✅ see test results below; SECURITY.md; DEPLOYMENT.md; CI workflow |
| 11 30-day validation | ✅ program + daily/weekly/final reports + strict verdict. **Not started**: it must run on a deployed instance for 30 real days |
| 12 Commercial SaaS prep | ✅ subscriptions, billing records, access gate, admin user/subscription management, landing + pricing. **Paid billing deliberately disabled** (`BILLING_ENABLED=false`); no payment provider integrated yet |

## Verified working (with evidence)

* **Live Binance data** (`data-api.binance.vision`): exchange info (3,702 spot symbols), 24h tickers, klines on 4 timeframes,
  book ticker, price. A full live scan cycle ran end-to-end: 12 candidates, 99 agent runs, 0 errors, **0 signals approved**.
  The market was in a broad downtrend (BTC 1h RSI ≈ 32, all of 1h/4h trending down), so zero signals is the
  correct outcome, not a bug. `api.binance.com` returned **HTTP 451** from the dev container's region; the client reports this clearly.
* **Dashboard in a real browser** (Chromium/Playwright): register → admin → run scan → every page, with no console errors.
  The mobile layout (390 px) has no horizontal scroll. The signal-detail chart rendered live Binance candles. The report-entry dialog worked.
  Signal pages were exercised with a clearly labelled **UI test-fixture signal in a throwaway scratch database**, never in the repo or any real DB.
* **PostgreSQL 16**: the full backend test suite passes on Postgres as well as SQLite.

## Tests

| Suite | Result |
|---|---|
| Backend `pytest` (SQLite) | **71 passed**, 0 failed |
| Backend `pytest` (PostgreSQL 16) | **71 passed**, 0 failed |
| Frontend `tsc` typecheck | passed |
| Frontend `vitest` | 3 passed |
| Frontend production build | passed |
| Browser E2E (Playwright, manual script) | passed, 0 console errors |
| `npm audit --omit=dev` | 2 moderate advisories remain in react-router v6 (open redirect via backslash with **untrusted** navigation targets; SSR hydration). The app never navigates to user-supplied URLs and does not use SSR; the fix requires the breaking v7 upgrade, which is accepted for now |
| Docker images | **not tested** (no Docker daemon in the dev container) |

Backend coverage: indicators (incl. Wilder RSI reference), Binance client (retries, 429 backoff, 451, endpoint
allow-list, payload validation, no API keys sent), every agent, Chief quotas/duplicates/vetoes, lifecycle
transitions, conservative TP/SL evaluation, expiry with partial data, paper costs, user report rules,
tracker, metrics, verdict strictness, report track separation, pipeline persistence/pause/limits/failure,
scan serialization, auth, RBAC, rate limiting, billing gate, settings validation, exports, Telegram webhook
secret + full link/enter/close flow (Bot API mocked), notifications.

Bugs found by the tests and fixed during development: paper trade not attached in-session (outcome skipped
closing it); a missing-level crash in the validator input; SQLite timezone loss; flat markets read as
"trending" because of float noise; the target engine not lifting the TP to the R/R requirement; a frontend render race (`data=null`).

## Still needs credentials or owner action

| Item | What to do |
|---|---|
| **Revoke the leaked Telegram token** | It is in git history (commit `500a4fb`). @BotFather → /revoke. See SECURITY.md |
| Telegram bot | Create a bot, set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_WEBHOOK_SECRET`, then call `setup-webhook`. Untested against real Telegram (api.telegram.org isn't reachable from the dev container); tested with a mocked Bot API |
| Production DB & hosting | Managed PostgreSQL + container host + Firebase Hosting (Firebase MCP was not logged in; nothing deployed) |
| News provider (optional) | `CRYPTOPANIC_API_KEY`. Without it Agent 07 reports NO_DATA and stays neutral. The integration is untested against the live CryptoPanic API |
| LLM narrative (optional) | `ANTHROPIC_API_KEY`. Untested live; signals publish fine without it |
| Payments (later) | Choose a provider (e.g. Stripe), implement checkout + webhooks writing `subscriptions`/`billing_records`, legal review (terms, privacy, risk disclosure, jurisdictions), then set `BILLING_ENABLED=true` |

## Before the 30-day test begins

1. Revoke the old Telegram token and configure the new one.
2. Deploy backend (Postgres, `ENVIRONMENT=production`, strong `SECRET_KEY`, `ENABLE_SCHEDULER=true` on one instance) and frontend.
3. Confirm the production region can reach Binance public data (Admin → Services shows no Binance error).
4. Enable DB backups and test a restore.
5. Link the owner's Telegram and send the test message.
6. Let the scheduler run ≥ 24 h with no errors; review a few rejected opportunities to sanity-check the rules.
7. Freeze trading settings, export them, then **Start 30-day program**.
8. Report ENTERED/SKIPPED/CLOSED for every signal, with real prices and fees.

## Recommended next engineering steps

* Alembic migrations before any schema change after go-live.
* Binance WebSocket price stream for lower-latency tracking. REST 1-minute polling is used now, which is adequate for 3–5% targets.
* Error tracking (Sentry), shared rate limiter for multi-instance, refresh tokens, email verification/reset, admin MFA.
* Upgrade to react-router v7 to clear the remaining moderate advisories.
