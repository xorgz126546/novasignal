# 30-day validation program

**Goal:** decide, with evidence, whether CRYPTONEX AI has a measurable edge **after realistic costs**. A few
winning trades are not evidence.

## Before day 1 (checklist)

- [ ] Backend deployed on PostgreSQL with backups enabled and a restore tested
- [ ] `ENABLE_SCHEDULER=true` on exactly one instance; scan cycles visible every 15 min for ≥ 24 h with no errors
- [ ] Binance status in Admin shows no errors from the production region
- [ ] Telegram webhook set, test message received, owner's chat linked
- [ ] Trading settings frozen (Admin → Settings) and exported. **Do not change rules during the 30 days**, or restart the program if you must
- [ ] Old Telegram token revoked (see SECURITY.md)
- [ ] Admin → 30-Day Validation → **Start 30-day program**

## During the program

* Every evaluated opportunity (approved **and rejected**) is stored with reasons.
* Every approved signal opens a **paper trade** automatically (fresh-price fill + slippage; 0.10% fee per side; stop exits with 2× slippage).
* The operator reports, for every signal, **ENTERED** (actual price, quantity, fees) or **SKIPPED**, and later **CLOSED**
  (exit price + fees), in Telegram or the dashboard. Unreported signals count as "no user response".
* Read the **daily report** each day and the **weekly report** each week (Validation page or `.md` download).

## Metrics (per track: theoretical · paper · actual, never combined)

win rate · loss rate · average win · average loss · profit factor · expectancy per trade with 95% CI ·
max drawdown · sum and compounded return · fees · slippage · signals generated vs executed vs skipped ·
average entry slippage vs signal price · top rejection reasons.

## Verdict rules (final report, paper track)

| Verdict | Condition |
|---|---|
| **INCONCLUSIVE** | fewer than 30 closed paper trades. *With ≤ 3 signals/day and zero-signal days, this is a likely outcome after 30 days. Extend the program rather than conclude.* |
| **EDGE INDICATED (not proven)** | expectancy > 0 **and** 95% CI lower bound > 0 **and** profit factor ≥ 1.2 **and** max drawdown < 15% |
| **NO EDGE DEMONSTRATED** | otherwise |

Also compare: the theoretical vs paper gap (the cost of execution), and the paper vs actual gap
(discipline and slippage in reality). Large gaps are findings in their own right.

## After the program

Fill in `docs/30_DAY_REPORT_TEMPLATE.md` from the final report and CSV exports. Paid subscriptions stay
disabled unless the verdict is at least "EDGE INDICATED" and the payment/legal checks in STATUS.md are done.
