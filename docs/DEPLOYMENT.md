# Deployment

**Simplest:** one service. The root `Dockerfile` builds the dashboard and serves it from the API on the same URL
(`STATIC_DIR`). `render.yaml` deploys that plus PostgreSQL on Render in one step; see [PHONE.md](PHONE.md).

Split layout (larger deployments):

| Component | Suggested host | Notes |
|---|---|---|
| PostgreSQL | Managed Postgres (Cloud SQL, Neon, Supabase-as-Postgres, RDS) | daily automated backups + PITR |
| Backend API + scheduler | One container (Cloud Run with min-instances=1, Fly.io, Render, a VPS) | `backend/Dockerfile`; run **exactly one** instance with `ENABLE_SCHEDULER=true` |
| Frontend | Firebase Hosting (`firebase.json` included) or any static host / the nginx image | build with `VITE_API_URL=https://api.your-domain` if the API is on another origin |

## 1. Configure secrets

Set the variables from `.env.example` in your host's **secret manager**, never in the repo:

* `SECRET_KEY`: 48+ random chars. The app refuses to start in production with the default.
* `DATABASE_URL=postgresql+psycopg://...`
* `ENVIRONMENT=production`, `CORS_ORIGINS=https://app.your-domain`, `PUBLIC_BASE_URL=https://app.your-domain`
* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` (random 32+ chars)
* optional `CRYPTOPANIC_API_KEY`, `ANTHROPIC_API_KEY`
* `BILLING_ENABLED=false` (keep it this way until payments and legal review are done)

The region matters: Binance blocks some jurisdictions. `data-api.binance.vision` is the public
market-data host. Check `GET /api/system/status` after deploy. `binance.last_error` must be empty.

## 2. Backend

```bash
docker build -t cryptonex-api ./backend
docker run -p 8000:8000 --env-file .env cryptonex-api
```

Tables are created automatically at startup (`create_all`). For schema changes after go-live, add Alembic
migrations before altering models. This is listed in STATUS.md as a pre-commercial task.

## 3. Frontend on Firebase Hosting

```bash
npm i -g firebase-tools
firebase login
firebase use --add            # select / create your project
cd frontend && VITE_API_URL=https://api.your-domain npm run build && cd ..
firebase deploy --only hosting
```

The Firebase MCP connector in the development session was **not authenticated**, so nothing was deployed
from there. Deploy with your own Firebase account using the commands above.

## <a id="telegram"></a>4. Telegram

1. Create a bot with **@BotFather** and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Set `TELEGRAM_WEBHOOK_SECRET` to a random string.
3. After the API is reachable over HTTPS, an admin calls:
   `POST /api/telegram/setup-webhook?public_api_url=https://api.your-domain` (with an admin JWT).
   The backend registers `https://api.your-domain/api/telegram/webhook` with `secret_token`. Updates
   without the matching `X-Telegram-Bot-Api-Secret-Token` header are rejected with 401.
4. Users link their chat: Settings → Telegram → Generate link code → send `/start CODE` to the bot.
5. Admin → Settings → "Send test message" verifies the full path.

No public URL yet? Run `python -m app.telegram.poll` instead of the webhook (polling mode).

## 5. Post-deploy checklist

- [ ] `GET /api/health` → ok
- [ ] Admin → Run scan now → cycle `ok` (0 approvals is fine), no Binance error in Services
- [ ] Telegram test message received
- [ ] Scheduler service shows `ok`, and scan cycles appear every 15 min
- [ ] Backups enabled and a restore tested (see SECURITY.md)
- [ ] Revoke the Telegram token leaked in git history (see README)
