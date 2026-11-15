# Using CRYPTONEX AI on your phone

There are two parts, and you can use either or both:

* **The dashboard**: a web app you open in your phone's browser and add to your home screen. It then opens full-screen like a normal app.
* **The Telegram bot**: signals arrive as Telegram messages with ENTERED / SKIPPED / CLOSED buttons.

Both need the backend running on a server on the internet. Your phone can't run the backend itself, and a
laptop is not required: every step below can be done from a phone browser.

## Step 1: Deploy once on Render (about 10 minutes, phone browser is fine)

1. Go to **render.com** and sign up with your GitHub account.
2. Tap **New → Blueprint** and choose the `novasignal` repository and the branch
   `claude/cryptonex-ai-trading-saas-evhe6f`. Merge it to `main` first if you prefer.
3. Render reads `render.yaml` and asks for a few values:
   * `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`: your admin login (password ≥ 10 characters, letters and digits).
     **Set these.** Otherwise the first person who registers on the public URL becomes admin.
   * `TELEGRAM_BOT_TOKEN`: the **new** token from @BotFather (revoke the old leaked one first). You can leave it empty and add it later.
4. Tap **Apply**. Render builds one service that serves both the dashboard and the API, plus a PostgreSQL database.
   The first build takes a few minutes.
5. Open the URL Render shows (like `https://cryptonex-ai-xxxx.onrender.com`) and sign in with the admin email and password.

Cost notes (Render's pricing, check their site): the blueprint uses the **Starter** web plan, which stays awake so
the scanner runs every 15 minutes. The **free** web plan sleeps after inactivity, which stops scanning and
tracking. The free database expires after 30 days, so pick a paid database plan for the 30-day validation.

Other hosts also work: Railway, Fly.io, Cloud Run, or any Docker host. They all use the root `Dockerfile`.

## Step 2: Install it on your home screen

* **iPhone (Safari):** open your Render URL → Share button → **Add to Home Screen**.
* **Android (Chrome):** open your Render URL → ⋮ menu → **Install app** (or **Add to Home screen**).

It opens full-screen with the CRYPTONEX icon and starts on the Overview page.

## Step 3: Connect Telegram (recommended)

1. Make sure `TELEGRAM_BOT_TOKEN` is set in Render (Environment tab). If you add it later, Render redeploys automatically.
2. In the dashboard: **Admin → Connect Telegram webhook** (one time). It shows "connected for @yourbot".
3. **Settings → Telegram → Generate link code**, then in Telegram send `/start YOUR_CODE` to your bot.
4. **Settings → Send test message.** If it arrives, Telegram is fully connected.

## Day-to-day on the phone

1. A signal arrives in Telegram (or on the **Live Signals** page).
2. If you buy it on Binance, tap **✅ ENTERED** and reply with your real price (and quantity), e.g. `68950 0.002`.
   If you don't, tap **⏭ SKIPPED**.
3. When you sell, tap **🏁 CLOSED** and reply with exit price and total fees in USDT, e.g. `71600 0.27`.
4. Check **Overview** and **Analytics** for results. Theoretical, paper and your actual trades are shown separately.

Days with no signals are normal. The system publishes nothing when no setup passes all 11 agents.
