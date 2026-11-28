# The 11 agents

Every agent subclasses `BaseAgent` (`backend/app/agents/base.py`) and has:

* `id`, `number`, `name`, `responsibility`
* a pydantic **Input** model and **Output** model. Both are validated; invalid input returns an error output instead of raising.
* `execute()`: pure, deterministic logic, unit-testable without network or database
* structured **logging** and a **recorder** hook that persists every run as an `agent_runs` row (status, pass, score, output, duration, error)
* **error handling**: any exception becomes `passed=false, error=...`, and the Chief rejects candidates with agent errors

The JSON schemas are served at `GET /api/agents/schema`. `score` ∈ [0,1] is an evidence-strength score, **not a probability**.

| # | Agent | Input | Output (key fields) | Pass rules (defaults; all configurable) |
|---|---|---|---|---|
| 01 | Market Scanner | 24h tickers, exchange info | `shortlist[]`, `excluded{reason:count}` | USDT quote, status TRADING, spot allowed; not stablecoin/leveraged token; 24h quote volume ≥ 20M; −15% ≤ 24h change ≤ +25%; 24h range ≥ 1.5%. Ranked by liquidity, tradable volatility, momentum |
| 02 | Technical Analyst | candles 15m/1h/4h/1d | indicators per TF (RSI, MACD, EMA 9/21/50/200, BB, ATR), `checks{}` | ≥ 5/8 checks and two hard rules: RSI(1h) in [45,70] **and** close > EMA21 > EMA50 |
| 03 | Trend Detector | candles | per-TF direction & ADX, `conflicts[]`, `sideways` | 1h up, 4h up, 1d not down, ADX(1h) ≥ 20. Direction needs EMA21–EMA50 separation > 0.1% and EMA50 slope > 0.05%/5 bars |
| 04 | Volume Intelligence | 1h candles, 24h volume, bid/ask | relative volume, z-score, OBV trend, spread | liquidity OK (volume & spread ≤ 0.10%), relative volume ≥ 0.8, no abnormal volume on a bearish candle |
| 05 | Support & Resistance | 1h/4h candles | supports/resistances (clustered swing points), nearest levels, breakout, **invalidation level** | room to resistance ≥ min target and a structural invalidation within reach |
| 06 | Price Action | 1h candles | patterns, structure (HH/HL), breakout-retest, momentum | structure not bearish, no bearish reversal pattern, 5-bar momentum > −1.5% |
| 07 | News & Sentiment | base asset | `status` OK/NO_DATA/ERROR, headlines, risk events, vote sentiment, `veto` | Only uses a real provider (CryptoPanic) if configured. **No data ⇒ neutral, never invented.** Vetoes on hack/delist/exploit/regulatory headlines |
| 08 | Risk Manager | levels, equity, limits, filters | quantity, notional, risk/reward in USDT, net R/R, `violations[]` | levels ordered; stop distance within [0.8%, 2.5%]; net R/R ≥ 2; open user trades < 1; open signals < 3; ≥ min notional. Size = 1% equity risk incl. fees+slippage, capped at 25% equity |
| 09 | Strategy Validator | all evidence outputs, 1h candles, proposed TP/SL %, past signal returns | agreement count, conflicts, **setup replay** stats, live track record | ≥ 4/5 evidence agents agree; no conflicts; replay net expectancy > 0 (if ≥ 5 samples); live theoretical expectancy not materially negative (if ≥ 10 signals) |
| 10 | Entry & Exit Engine | price, ATR 1h/4h, invalidation, resistance, tick size, costs | entry, **one TP**, **one SL**, expected move, gross & net R/R, net expected return | SL at structure (≥ max(0.8%, 1.2×ATR1h)), ≤ 2.5%; TP within 3–5% and below resistance, raised to the R/R requirement only if within 3.5×ATR(4h); net R/R after 0.30% round-trip costs ≥ 2 |
| 11 | Chief Trading Officer | all dossiers + state | decisions with confidence, breakdown, rejection reasons, justification | every mandatory agent passed without error; not paused; no open signal / cooldown on the symbol; confidence ≥ 60; daily quota (3) filled by highest confidence first |

## Setup replay (Agent 09)

It walks the symbol's own 1h history. At each bar where the same core setup holds (close > EMA21 > EMA50,
MACD histogram > 0, RSI 45–70), it simulates the **proposed** TP/SL percentages forward up to the signal horizon:
SL first if both levels fall in one candle, and non-overlapping samples only. A horizon with insufficient future data
is discarded, never extrapolated. It is a sanity check of the setup on this market, not a full backtest.

## Where LLMs are used

Only `llm/narrative.py`, which is optional. After a signal is approved, it may write a ≤ 90-word explanation
from the agents' structured facts. It cannot change any number, and a missing key, failure or refusal simply
leaves the narrative empty.
