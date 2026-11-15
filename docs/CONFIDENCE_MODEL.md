# Confidence model

```
confidence = 100 × Σ  w_a × score_a
```

| Agent | Weight w_a |
|---|---|
| 02 Technical Analyst | 0.20 |
| 03 Trend Detector | 0.20 |
| 04 Volume Intelligence | 0.15 |
| 05 Support & Resistance | 0.15 |
| 06 Price Action | 0.15 |
| 09 Strategy Validator | 0.15 |

* Each `score_a ∈ [0,1]` is computed deterministically by the agent (documented in its module), for example
  the fraction of technical checks satisfied, or a timeframe-weighted trend alignment scaled by ADX strength.
* **07 News** contributes nothing, because data is often unavailable and we refuse to invent sentiment. It can only **veto**.
* **08 Risk** and **10 Entry/Exit** are hard gates, not confidence inputs.
* A signal is only approved when every mandatory agent passes **and** confidence ≥ `min_confidence` (default 60).

## What it is not

The confidence score is **not a probability of profit** and is never displayed as one. A score of 72 means
"72% of the maximum evidence the model can see", not "72% chance to win". It will only become calibrated
evidence when the 30-day validation shows how score bands relate to outcomes. The Agent Monitor already shows
each agent's average score on winners vs losers for this purpose.

## Testing

`tests/test_agents.py::test_confidence_score_formula` and `test_confidence_weights_sum_to_one` lock the formula.
Each signal stores its full `confidence_breakdown`, which is shown on the Signal Details page.
