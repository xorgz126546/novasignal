# Security

## Principles

* **Least privilege with Binance.** Only public market-data GET endpoints are used, through an allow-list in
  `BinanceClient._get`. No API key is requested, stored or sent. Nothing can place orders or withdraw funds.
* **No automatic execution.** Users execute manually; the system only records what they report.
* **Secrets only in the environment** (`config.py`). `.env*` is git-ignored and `.env.example` holds placeholders.
  CI includes a check that blocks committed Telegram tokens.

## ⚠️ Known incident: leaked Telegram token

The original `bot.py` (commit `500a4fb`) contained a hard-coded Telegram bot token. The working tree no longer
contains it, but **it remains in git history**. Required actions:

1. Revoke it now: @BotFather → `/revoke` → choose the bot, then use the new token only via `TELEGRAM_BOT_TOKEN`.
2. Optionally purge it from history (`git filter-repo`) and force-push; coordinate with anyone who has clones.

## Controls implemented

| Control | Implementation |
|---|---|
| Authentication | bcrypt (cost 12) password hashes; JWT HS256 with expiry; min 10-char passwords with letters+digits |
| Authorization | `require_admin` / `require_subscriber` dependencies; admin-only trading settings, user management, exports, scans; users can only read/modify their own trades |
| Access control (billing) | `has_active_access`: once `BILLING_ENABLED=true`, non-admins need an active/trial subscription with a future period end (402 otherwise) |
| Input validation | pydantic models on every endpoint; trading config validated with cross-field rules; Telegram inputs parsed as numbers and sanity-checked (entry within ±20% of signal) |
| Rate limiting | global 600 req/min/IP; login 10/5 min; register 5/h; Telegram webhook 120/min; Binance client weight budget + `Retry-After` backoff on 429/418 |
| Telegram webhook | constant-time comparison of `X-Telegram-Bot-Api-Secret-Token`; 100 KB payload cap; always 200 after auth to avoid retry storms; bot token never appears in errors |
| Audit log | register, login (incl. failures), settings changes (before/after), pause/resume, scans, exports, user changes, service restarts, trade reports |
| HTTP headers | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, HSTS in production; API docs disabled in production |
| Error handling | unhandled exceptions → generic 500 (details only in server logs); agent errors are captured per run and surfaced to admins |
| Frontend | holds only the user's JWT; no exchange keys, bot tokens or admin credentials are ever sent to it |
| Production guard | the app refuses to start in production with the default `SECRET_KEY` |

## Database security

* The production database is PostgreSQL behind the API. No client connects to it directly, so there are no
  client-side "security rules" to maintain (that concept applies to Firestore, which is not used).
* Use a dedicated DB role with privileges only on the app database, TLS (`sslmode=require`), and a private network/VPC where available.

## Backup strategy

* Managed Postgres with **daily automated backups + point-in-time recovery (≥ 7 days)**.
* Weekly logical dump (`pg_dump -Fc`) to separate storage, kept 30 days. This covers the full 30-day validation dataset.
* **Test a restore** before the validation program starts and monthly afterwards.
* Admin → Exports (CSV of signals, paper trades, user trades and opportunities) gives an extra, human-readable archive of the validation evidence.

## Monitoring

* `service_states` (scanner, tracker, telegram, scheduler) are visible in Admin → Services, and failures send a Telegram **system alert** to admins.
* `GET /api/health` for uptime checks; container `HEALTHCHECK` in the Dockerfile.
* Recommended addition before commercial launch: an error-tracking service (e.g. Sentry) via its FastAPI integration, configured by DSN in the environment.

## Known limitations / follow-ups

* The rate limiter is in-process. Behind multiple instances, add a shared limiter (proxy/CDN or Redis).
* JWTs are stateless (12 h default). Disabling a user takes effect on the next request, but a stolen token stays valid until expiry. Consider shorter TTL + refresh tokens for the commercial phase.
* No email verification or password reset flow yet (needs an email provider).
* No MFA for admins yet.
