# Connectors & skills usage report

## Actually used

| Tool / skill | Used for | Verification evidence |
|---|---|---|
| Bash, file tools | all code, tests, docs | this repository |
| `claude-code-remote` environment tools | created a personal environment **"novasignal"** allowing `api.binance.com` + `data-api.binance.vision` (approved by the user) and moved this session onto it | `data-api.binance.vision/api/v3/ping` → 200; `api.binance.com` → 451 (region) |
| Binance public API (`data-api.binance.vision`) | live integration test of the client + full scan cycle | 3,702 symbols; 12-candidate scan, 99 agent runs, 0 errors, 0 approvals |
| `claude-api` skill | optional narrative module uses the official `anthropic` SDK, `claude-opus-5-5`, `effort: low`, server-side refusal fallbacks, refusal handling | SDK signature check (`fallbacks`, `output_config`, `betas`); **not called live** (no API key) |
| `dataviz` skill | chart rules; palette validator run against the dark surface | validator: brand red vs loss red ΔE 3.5 → brand red removed from data marks; green/red ΔE 7.4 (deutan) → polarity also encoded by baseline position and signed values |
| Chromium + Playwright | E2E run of the built frontend against the live backend; screenshots reviewed | 0 console errors after fixes; mobile scrollWidth = 390 |
| PostgreSQL 16 | test suite on Postgres | 71/71 passed |
| ListConnectors, Firebase MCP `get_environment` | discovery | see AVAILABLE_TOOLS_AND_SKILLS.md |
| git | commits/push to `claude/cryptonex-ai-trading-saas-evhe6f` | branch on GitHub |

## Integrations status

| Integration | Status |
|---|---|
| Binance Spot public market data | ✅ **connected & tested live** |
| PostgreSQL | ✅ tested locally (production instance pending) |
| Telegram Bot API | ⏳ implemented, **tested with a mocked Bot API only**; needs a bot token + public HTTPS URL |
| Firebase Hosting | ⏳ `firebase.json` ready; Firebase MCP unauthenticated, **nothing deployed** |
| CryptoPanic news | ⏳ implemented; tested with a fake fetcher; needs API key |
| Anthropic (narrative) | ⏳ implemented; needs API key; optional |
| Payments (Stripe or other) | ❌ not integrated by design; billing disabled until validation + legal checks |
| Monitoring (Sentry etc.) | ❌ not integrated; recommended in SECURITY.md |
| Analytics plugins (Amplitude etc.) | ❌ failed to connect (proxy 403); not needed for MVP |

## Not used, and why

* **Firebase MCP deploy**: no authenticated Firebase user or project; deploying is an outward-facing action for the owner.
* **Canva / Claude Docs / Graphify / Remote Desktop Commander**: not needed for the deliverables, which are code and repo docs.
* **Sub-agents**: phases share schemas; sequential work avoided conflicting edits.
* **Docker**: no daemon available, so images were not built.

## Security permissions granted during development

* Network allow-list: `api.binance.com`, `data-api.binance.vision` on a new personal environment (user-approved).
* No credentials were requested, created or stored. No Binance API keys, Telegram tokens or payment details were used.

## Tests performed

See `docs/STATUS.md#tests`: 71 backend tests (SQLite + PostgreSQL), frontend typecheck + unit tests + production
build, browser E2E, `npm audit`, live Binance scan.
