"""AGENT 09 — STRATEGY VALIDATOR: independent verification.

1. Indicator/agent agreement count.
2. Conflicting-evidence detection.
3. Historical check A: walk-forward replay of the SAME setup rule on this symbol's 1h history with
   the proposed TP/SL percentages (conservative: if TP and SL fall in one candle, SL counts first).
4. Historical check B: realized theoretical expectancy of past CRYPTONEX signals (from the DB).
"""
from __future__ import annotations

from ..market import indicators as ta
from ..market.binance import Candle
from .base import AgentInput, AgentOutput, BaseAgent, clamp01

EVIDENCE_AGENTS = ("technical_analyst", "trend_detector", "volume_intelligence", "support_resistance", "price_action")


class ValidatorInput(AgentInput):
    symbol: str
    agent_results: dict[str, dict]  # agent_id -> {"passed": bool, "score": float, ...}
    candles_1h: list[Candle]
    tp_pct: float
    sl_pct: float
    round_trip_cost_pct: float = 0.3
    min_agreement: int = 4
    horizon_bars: int = 48
    past_signal_returns: list[float] = []  # theoretical % returns of resolved signals


class ValidatorOutput(AgentOutput):
    agreement: int = 0
    conflicts: list[str] = []
    backtest: dict = {}
    live_track_record: dict = {}


def replay_setup(candles: list[Candle], tp_pct: float, sl_pct: float, horizon: int = 48) -> dict:
    closes = [c.close for c in candles]
    e21, e50 = ta.ema(closes, 21), ta.ema(closes, 50)
    _, _, hist = ta.macd(closes)
    r = ta.rsi(closes)
    wins = losses = timeouts = 0
    rets: list[float] = []
    i = 60
    while i < len(candles) - 1:
        if None in (e21[i], e50[i], hist[i], r[i]) or not (closes[i] > e21[i] > e50[i] and hist[i] > 0 and 45 <= r[i] <= 70):
            i += 1
            continue
        entry = closes[i]
        tp, sl = entry * (1 + tp_pct / 100), entry * (1 - sl_pct / 100)
        outcome, j = None, i + 1
        while j < min(len(candles), i + 1 + horizon):
            c = candles[j]
            if c.low <= sl:
                outcome = "loss"
                break
            if c.high >= tp:
                outcome = "win"
                break
            j += 1
        if outcome is None:
            if j >= len(candles):  # not enough future data: discard (no look-ahead fabrication)
                break
            timeouts += 1
            rets.append((candles[j - 1].close - entry) / entry * 100)
        elif outcome == "win":
            wins += 1
            rets.append(tp_pct)
        else:
            losses += 1
            rets.append(-sl_pct)
        i = j + 1  # non-overlapping samples
    n = len(rets)
    return {"samples": n, "wins": wins, "losses": losses, "timeouts": timeouts,
            "win_rate": wins / n if n else None, "avg_return_pct": sum(rets) / n if n else None}


class StrategyValidator(BaseAgent[ValidatorInput, ValidatorOutput]):
    id = "strategy_validator"
    number = 9
    name = "Strategy Validator"
    responsibility = "Independently verify agreement, detect conflicting evidence, and check historical performance of the setup."
    Input = ValidatorInput
    Output = ValidatorOutput

    def execute(self, i: ValidatorInput) -> ValidatorOutput:
        res = i.agent_results
        agreement = sum(1 for a in EVIDENCE_AGENTS if res.get(a, {}).get("passed"))
        conflicts = []
        if res.get("technical_analyst", {}).get("passed") and not res.get("trend_detector", {}).get("passed"):
            conflicts.append("bullish indicators but higher-timeframe trend not confirmed")
        if res.get("trend_detector", {}).get("passed") and not res.get("volume_intelligence", {}).get("passed"):
            conflicts.append("trend without volume confirmation")
        if res.get("price_action", {}).get("structure") == "bearish":
            conflicts.append("bearish 1h market structure")
        if res.get("news_sentiment", {}).get("veto"):
            conflicts.append("news risk event")
        bt = replay_setup(i.candles_1h, i.tp_pct, i.sl_pct, i.horizon_bars)
        cost = i.round_trip_cost_pct
        bt_ok = True
        reasons = [f"{agreement}/{len(EVIDENCE_AGENTS)} evidence agents agree"]
        if bt["samples"] >= 5:
            exp_net = bt["avg_return_pct"] - cost
            bt["expectancy_net_pct"] = exp_net
            bt_ok = exp_net > 0
            reasons.append(f"setup replay on {bt['samples']} past 1h setups: win rate {bt['win_rate']:.0%}, "
                           f"net expectancy {exp_net:+.2f}%/trade")
        else:
            reasons.append(f"setup replay: only {bt['samples']} samples — insufficient, not used as evidence")
        live = {"samples": len(i.past_signal_returns)}
        live_ok = True
        if len(i.past_signal_returns) >= 10:
            live["expectancy_pct"] = sum(i.past_signal_returns) / len(i.past_signal_returns)
            live_ok = live["expectancy_pct"] - cost > -0.5
            reasons.append(f"live theoretical expectancy {live['expectancy_pct']:+.2f}% over {live['samples']} signals")
            if not live_ok:
                reasons.append("live track record is materially negative — system-level caution")
        reasons += [f"conflict: {c}" for c in conflicts]
        bt_score = 0.5 if bt["samples"] < 5 else clamp01(0.5 + bt["expectancy_net_pct"] / 4)
        score = clamp01(0.5 * agreement / len(EVIDENCE_AGENTS) + 0.35 * bt_score + 0.15 * (1.0 if not conflicts else 0.0))
        passed = agreement >= i.min_agreement and not conflicts and bt_ok and live_ok
        return ValidatorOutput(passed=passed, score=round(score, 4), agreement=agreement, conflicts=conflicts,
                               backtest=bt, live_track_record=live, reasons=reasons)
