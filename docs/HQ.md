# CRYPTONEX HQ — the 15-agent company

HQ is a layer on top of CRYPTONEX AI. You talk to **one** agent, the Manager (Maya). She delegates to 14
specialists, and you watch them work in a live pixel-art office (`/app/hq`). The agents keep improving from
real results and from GitHub. **Nothing they learn or change goes live without your approval.**

## Hard rules (enforced in code, not only in prompts)

| Rule | Where it is enforced |
|---|---|
| Binance Spot USDT only, public data, no keys, no orders | `market/binance.py` endpoint allow-list; HQ has no order code at all |
| Every signal: entry, ONE take-profit, stop, invalidation, risk %, reasons; otherwise NO TRADE + reason | `hq/strategies/base.plan_levels`, `hq/agents/publisher.py`, `hq_decisions` table |
| Numbers only from deterministic code | Strategies/backtests are pure code; `llm.check_numbers` rejects LLM text containing numbers that are not in the facts |
| Untrusted GitHub/web content | `hq/guard.py` (wrap, sanitize, injection tripwires); model output accepted only via strict schemas; never executed/installed |
| License hygiene | `guard.classify_license`: MIT/Apache-2.0/BSD/ISC = adaptable with attribution; everything else = ideas only; `PROVENANCE.md` |
| Nothing live without approval | `hq/approvals.py` + `hq/versions.py`: agents only create *inactive* candidate versions; approve activates; one-click rollback |
| Never touch `main`, never merge | `hq/github.py` writes only to `agent/*` branches and opens **draft** PRs; it has no merge method |
| XP only from verified events | `hq/xp.py`: fixed source types, unique `(agent, source_type, source_ref)`; no API to award XP |
| Cost control | per-agent model, `HQ_LLM_DAILY_BUDGET_USD` ledger (`hq_llm_usage`), pre-flight cost check, template fallback |
| Disclaimer everywhere | UI, every signal (justification + Telegram), every draft, every brief |

## The 15 agents (`backend/app/hq/config/agents.yaml`)

Adding, removing or re-rooming an agent is a change to that YAML file (max 15). Each agent has: id, AR/EN name,
role, room, model (routine/senior), tools with the minimum level per tool, schedule, a prompt
(`hq/prompts/<id>.md`) and an approved skills file (DB, mirrored to `skills/<id>.md`).

Manager · Scanner · Structure Analyst · Breakout+Retest · Trend Pullback · Liquidity Sweep · Risk Manager (veto) ·
Backtester · Publisher · Auditor/Watchdog · Engineer · GitHub Scout · Trend Hunter · Content Editor · Librarian/Coach.

## The four loops

**A — Trading** (`hq/loops/trading.py`):
1. The Scanner builds the universe (liquidity, volatility, spread).
2. The Structure Analyst reads the 4H/1H/15M context (EMA, RSI, MACD, ATR, ADX, BOS/CHoCH).
3. Three independent strategy plug-ins, plus approved rule strategies, evaluate each market.
4. The Risk Manager applies the BTC filter, exposure limits and sizing, and holds the **veto**.
5. The Manager ranks the setups and applies the daily quota.
6. The Publisher deduplicates, scores 0–100 against a configurable minimum, and publishes in-app and on Telegram.

Every candidate becomes a persisted `SIGNAL` or `NO_TRADE` with its stage and reasons.

**B — Growth** (`hq/loops/growth.py`):
1. The Trend Hunter collects Binance movers, CoinGecko trending coins and GitHub repos.
2. The Content Editor writes Arabic and English posts, reel scripts and an SVG signal card.
3. Each draft waits in "Needs you". Hype and profit promises are detected and discarded. Nothing is ever auto-posted.

**C — GitHub Upgrade** (`hq/loops/upgrade.py`, priority):
1. The Scout searches public, non-archived, active repos.
2. It writes a knowledge note: URL, license, commit SHA, the idea in our own words, and a test plan.
3. The note becomes a proposal: a rule strategy (a safe JSON DSL over whitelisted features), a parameter change, or a prompt addition.
4. The Backtester runs its gate: walk-forward, out-of-sample, fees and slippage, minimum trades, baseline comparison, and an overfitting penalty based on how many variants were tried.
5. The Manager writes a plain-Arabic summary, and the proposal appears in "Needs you".
6. If you approve, a versioned deploy happens, with rollback and XP. If you reject, the lesson is kept.

**D — Repair** (`hq/loops/repair.py`):
1. The Watchdog opens a deduplicated incident: feed down, stale data, repeated errors, or a failed job.
2. The Engineer diagnoses it. With an LLM and a GitHub token, it writes a validated minimal fix plus a regression test on `agent/fix-<id>` as a draft PR, and CI runs.
3. The Manager reports the problem, the cause and the fix, with the PR link.
4. You merge on GitHub. The app verifies the merge and awards XP.

**Demo:** **Inject test failure** (owner-only) forces the Binance client to fail N calls, so the real detection → diagnosis → report path runs.

## Daily best pick + strategy tournament (`hq/loops/daily_pick.py`)

**8 strategies** live across the three strategy agents:

| Agent | Strategies |
|---|---|
| Bilal | Breakout+Retest, Volatility Squeeze Breakout, 4H Donchian Breakout |
| Tara | Trend Pullback, Relative-Strength Leader (coins beating BTC by 5%+ over 7 days) |
| Layla | Liquidity Sweep, Oversold Bounce, Range-Support Bounce |

- **Tournament** (Sundays 05:00, and before the first pick): every strategy runs the walk-forward gate on real
  history. Each gets a grade: **A** passed · **B** positive, not proven · **C** too few trades · **D** slightly negative · **F** clearly losing.
- **Daily best pick** (09:00 Africa/Algiers, `HQ_DAILY_PICK_TIME`):
  - All strategies scan a wider universe (25 markets).
  - Every setup must still pass the hard rules and the Risk veto.
  - The best setup by score × evidence factor (A ×1.15 … D ×0.85; F excluded) is published as **⭐ DAILY BEST PICK**, with its evidence grade shown honestly.
  - If nothing valid exists, you get NO TRADE plus a watchlist, clearly marked as not a recommendation.
- **BTC risk-off:** only *decoupled leaders* (beating BTC by 5%+ over 7 days, in their own 4H uptrend) may pass, and only at **half risk and half position size**.
- This runs even when `HQ_ENABLED=false` (set `HQ_DAILY_PICK=false` to stop it).

## Reports (`hq/reports.py`)

- **Per-agent daily report:** numbers from the database, problems, proposed solutions, and what the agent needs from you.
- **Morning brief at 08:00 Africa/Algiers:** sections for your part today (a persisted checklist), Needs you, What broke (with the fix), Level-ups, yesterday's results in 3 lines, and top trends.
- **Weekly Library report** (Friday).
- **Instant critical alerts.**

All of these are stored, readable in the Reports tab, downloadable as `.md`, and sent to `TELEGRAM_CHAT_ID`. Reports
are mirrored to `reports/YYYY-MM-DD/` in the repo by the weekly Library PR.

## Levels

Lv1–Lv10 (thresholds in `hq/registry.py`). XP comes only from verified events:

| Event | XP |
|---|---|
| TP hit | 40 |
| NO TRADE that avoided a loss | 25 |
| Bug confirmed | 20 |
| Fix merged | 60 |
| Backtest passed | 30 |
| Owner approval | 50 |

A higher level gives a bounded scoring weight (+1% per level, so Lv10 = ×1.09) and unlocks tools listed in the YAML.

## Turning it on (Render → Environment)

1. Add `ANTHROPIC_API_KEY`, your `TELEGRAM_CHAT_ID`, and a fine-grained `GITHUB_TOKEN`.
   The token should be scoped to this repo only, with Contents + Pull requests set to read/write.
2. Set `HQ_ENABLED=true`. The schedule then starts:
   - a trading cycle every 30 min
   - the auditor every 10 min
   - agent reports at 07:30
   - the brief at 08:00
   - trends at 09:00 and 18:00
   - drafts at 10:00
   - the Scout on Mondays
   - baseline backtests and the Library session on Fridays
   - a PR watch every 30 min
3. Without these keys, HQ still runs every deterministic part (cycles, backtests, audits, reports with templates).
   The LLM wording, distillation and fix PRs are simply skipped.

## Honest status

- Over 125 days of 1H data on 5 major pairs, **none of the three built-in strategies passed the gate**. The evidence does not support an edge yet, and the system says so instead of publishing.
- Live LLM calls, the GitHub README fetch and Telegram delivery could not be exercised from the development sandbox. They are covered by mocked tests and run on Render once the keys are added.
