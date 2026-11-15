# CRYPTONEX AI — 30-day validation report

> Fill every field from `GET /api/validation/{id}/report?kind=final&format=md` and the Admin CSV exports.
> Leave a field "n/a" rather than estimating. Never merge the three tracks.

## 1. Program

| Field | Value |
|---|---|
| Program window (UTC) | ____ → ____ |
| Code version (git commit) | ____ |
| Trading settings snapshot attached | yes / no |
| Settings changed during program? | no / yes (describe) |
| Downtime / data gaps | ____ |

## 2. Funnel

| Item | Count |
|---|---|
| Scan cycles run | |
| Opportunities evaluated | |
| Rejected | |
| Signals approved | |
| Days with zero signals | |
| Executed by user | |
| Skipped by user | |
| No user response | |

Top rejection reasons: ____

## 3. Results by track

| Metric | Theoretical | Paper (fees+slippage) | Actual (user-confirmed) |
|---|---|---|---|
| Closed trades | | | |
| Win rate | | | |
| Avg win | | | |
| Avg loss | | | |
| Profit factor | | | |
| Expectancy / trade | | | |
| Expectancy 95% CI | | | |
| Max drawdown | | | |
| Sum of returns | | | |
| Net P/L (USDT) | n/a | | |
| Fees (USDT) | 0 | | |
| Slippage cost (USDT) | 0 | | |

Average entry slippage vs signal price (actual): ____ %

## 4. Verdict (paper track rules)

**INCONCLUSIVE / EDGE INDICATED / NO EDGE DEMONSTRATED** — reason: ____

## 5. Analysis

* Theoretical → paper gap (cost of execution): ____
* Paper → actual gap (real fills, discipline): ____
* Agent contribution (Agent Monitor: avg score winners vs losers): ____
* Confidence bands vs outcome: ____
* Market regime during the window (trend / range / high volatility): ____

## 6. Decision

- [ ] Continue testing for another ___ days
- [ ] Adjust rules (requires a fresh program)
- [ ] Proceed to commercial preparation (only with EDGE INDICATED + payments/legal verified)

_Past and simulated performance does not guarantee future results._
