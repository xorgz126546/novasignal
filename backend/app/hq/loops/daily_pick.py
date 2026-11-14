"""Strategy tournament + Daily Best Pick.

Tournament (Backtester): every active strategy runs the walk-forward gate on real history → leaderboard and an
evidence grade per strategy (A passed · B positive · C untested/few trades · D negative · F clearly losing).

Daily Best Pick (Manager): once a day, ALL strategies scan the market; every setup must still pass the hard
rules (one TP, stop bounds, min net R:R, Risk veto). The best one by score × evidence is published as the
day's recommendation. Strategies graded F are excluded. If nothing valid exists: NO TRADE + a watchlist
(clearly marked as NOT a recommendation).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...agents import MarketScanner
from ...engine.pipeline import utc_day_start
from ...market.binance import BinanceClient, MarketDataError
from ...models import TERMINAL_STATUSES, Signal, utcnow
from ...trading_config import load_config
from .. import bus, llm, runtime
from ..agents import publisher as pub
from ..agents import risk as riskmod
from ..agents.backtester import run_gate
from ..data import fetch_context
from ..models import HQBacktestRun, HQDecision, HQReport
from ..registry import ensure_agents
from ..reports import local_day, md_to_telegram, save_report
from ..strategies.base import Reject
from ..strategies.registry import active_strategies
from ..versions import active_data

log = logging.getLogger("cryptonex.hq.daily_pick")
GRADE_FACTOR = {"A": 1.15, "B": 1.05, "C": 0.95, "D": 0.85}
DEFAULT_PICK_MIN_SCORE = 50.0


# ---------------- tournament ----------------
def evidence(db: Session, strategy_id: str) -> dict:
    run = db.scalar(select(HQBacktestRun).where(HQBacktestRun.subject.startswith(f"strategy:{strategy_id}@"))
                    .order_by(HQBacktestRun.id.desc()).limit(1))
    if not run:
        return {"grade": "C", "label": "untested yet", "trades": 0}
    s = run.result.get("summary") or {}
    n, e = s.get("trades") or 0, s.get("expectancy_pct")
    if run.passed:
        g = "A"
    elif n < 10 or e is None:
        g = "C"
    elif e > 0:
        g = "B"
    elif e > -0.5:
        g = "D"
    else:
        g = "F"
    labels = {"A": "passed the walk-forward gate", "B": "positive but not yet proven", "C": "too few trades to judge",
              "D": "slightly negative history", "F": "clearly losing history (excluded)"}
    return {"grade": g, "label": labels[g], "trades": n, "win_rate": s.get("win_rate"), "expectancy_pct": e,
            "at": run.created_at.isoformat(), "run_id": run.id}


async def run_tournament(db: Session, client: BinanceClient, notify_owner=None, universe: list[str] | None = None) -> HQReport:
    rows = []
    for act in active_strategies(db):
        run = await run_gate(db, client, act.strategy, act.params, f"strategy:{act.strategy.id}@tournament", universe=universe)
        s = run.result.get("summary") or {}
        rows.append((act, run, s))
    rows.sort(key=lambda r: (r[1].passed, r[2].get("expectancy_pct") or -99, r[2].get("trades") or 0), reverse=True)
    lines = ["# Strategy tournament", "", "| # | Strategy | Agent | Trades | Win rate | Net exp./trade | Grade |", "|---|---|---|---|---|---|---|"]
    for i, (act, run, s) in enumerate(rows, 1):
        ev = evidence(db, act.strategy.id)
        wr = f"{s['win_rate'] * 100:.0f}%" if s.get("win_rate") is not None else "—"
        ex = f"{s['expectancy_pct']:+.2f}%" if s.get("expectancy_pct") is not None else "—"
        lines.append(f"| {i} | {act.strategy.name} | {act.agent_id} | {s.get('trades', 0)} | {wr} | {ex} | {ev['grade']} |")
    lines += ["", "Grades: A passed the gate · B positive, not proven · C too few trades · D slightly negative · F excluded.",
              "_Real Binance history, fees and slippage included. Past results do not guarantee future results._"]
    rep = save_report(db, "tournament", "Strategy tournament", "\n".join(lines),
                      {"leaderboard": [{"strategy": a.strategy.id, "passed": r.passed, **{k: s.get(k) for k in ("trades", "win_rate", "expectancy_pct")}}
                                       for a, r, s in rows]}, "backtester")
    if notify_owner:
        await notify_owner(md_to_telegram("\n".join(lines)))
    return rep


def leaderboard(db: Session) -> list[dict]:
    return [{"strategy": a.strategy.id, "name": a.strategy.name, "agent": a.agent_id, **evidence(db, a.strategy.id)}
            for a in active_strategies(db)]


# ---------------- trigger watchlist (conditions, NOT recommendations) ----------------
def triggers(contexts: dict) -> list[dict]:
    """Markets closest to triggering a strategy, with the exact deterministic condition to watch."""
    out = []
    for sym, ctx in contexts.items():
        f, cs = ctx.features, ctx.candles.get("1h", [])
        if not cs:
            continue
        px = cs[-1].close
        if f.get("rsi_min6_1h", 100) < 30:
            lvl = cs[-1].high
            out.append({"symbol": sym, "strategy": "oversold_bounce", "distance_pct": round((lvl - px) / px * 100, 2),
                        "trigger": f"1H close above {lvl:.6g} with RSI(1h) ≥ 35 (was oversold at {f['rsi_min6_1h']:.1f})"})
        if "donchian_high20_4h" in f and f.get("adx_4h", 0) >= 20 and f.get("ema21_4h", 0) > f.get("ema50_4h", 0):
            lvl = f["donchian_high20_4h"]
            out.append({"symbol": sym, "strategy": "donchian_breakout", "distance_pct": round((lvl - px) / px * 100, 2),
                        "trigger": f"4H close above {lvl:.6g} (20-bar 4H high)"})
        if f.get("squeeze_recent_1h") and "donchian_high20_1h" in f:
            lvl = f["donchian_high20_1h"]
            out.append({"symbol": sym, "strategy": "squeeze_breakout", "distance_pct": round((lvl - px) / px * 100, 2),
                        "trigger": f"1H close above {lvl:.6g} on 1.3x+ volume after a volatility squeeze"})
    return sorted([t for t in out if t["distance_pct"] >= 0], key=lambda t: t["distance_pct"])[:5]


# ---------------- daily best pick ----------------
async def run_daily_pick(db: Session, client: BinanceClient, notify=None, notify_owner=None, now=None) -> dict:
    now = now or utcnow()
    day = local_day(now)
    ref = f"pick-{day}"
    done = db.scalar(select(HQDecision).where(HQDecision.cycle_ref == ref, HQDecision.decision == "SIGNAL"))
    if done:
        return {"status": "already_picked", "signal_id": done.signal_id}
    ensure_agents(db)
    cfg = load_config(db)
    risk = cfg.risk
    if cfg.paused:
        return {"status": "paused"}
    signals_today = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= utc_day_start(now))) or 0
    if signals_today >= risk.max_signals_per_day:
        return {"status": "daily_limit_reached"}
    bus.publish(db, "cycle_started", "manager", cycle_ref=ref, kind="daily_pick")

    with runtime.task(db, "scanner", "scan", "Daily pick: scanning a wider USDT universe") as t:
        symbols = await client.exchange_info()
        tickers = await client.tickers_24h()
        scan = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=risk.min_quote_volume_usdt,
                                        excluded_bases=cfg.excluded_bases, shortlist_size=max(risk.shortlist_size, 25)))
        universe = [c.symbol for c in scan.shortlist]
        t.result = {"summary": f"{len(universe)} markets for the daily pick"}

    contexts = {}
    with runtime.task(db, "structure", "structure", f"Daily pick: 4H/1H/15M structure for {len(universe)} markets") as t:
        try:
            btc = await fetch_context(client, "BTCUSDT", symbols.get("BTCUSDT"))
        except MarketDataError:
            btc = None
        for sym in universe:
            try:
                contexts[sym] = await fetch_context(client, sym, symbols.get(sym))
            except MarketDataError:
                continue
        for c in contexts.values():
            c.extras["btc_ret_7d"] = btc.features.get("ret_168_1h") if btc else None
        t.result = {"summary": f"{len(contexts)} markets ready"}

    candidates = []  # (final_score, setup, agent, sizing, reasons, ev, raw_score)
    near_miss = []   # watchlist: valid levels but stopped later
    open_sigs = list(db.scalars(select(Signal).where(Signal.status.not_in([s.value for s in TERMINAL_STATUSES]))))
    btc_ok = riskmod.btc_filter(btc)
    for act in active_strategies(db):
        ev = evidence(db, act.strategy.id)
        with runtime.task(db, act.agent_id, "strategy", f"Daily pick: {act.strategy.name} ({ev['grade']})") as t:
            found = 0
            for sym, ctx in contexts.items():
                r = act.strategy.evaluate(ctx, act.params, risk)
                if isinstance(r, Reject):
                    continue
                found += 1
                v = riskmod.assess(r, ctx, risk, btc_ok, len(open_sigs), {x.symbol for x in open_sigs}, signals_today,
                                   riskmod.is_decoupled(ctx, btc))
                raw, breakdown = pub.score(r, ctx, risk, pub.agent_level(db, act.agent_id))
                if not v.approved:
                    near_miss.append((raw, r, "risk veto: " + "; ".join(v.reasons[-2:])))
                    continue
                if ev["grade"] == "F":
                    near_miss.append((raw, r, f"strategy excluded: {ev['label']}"))
                    continue
                dup = pub.is_duplicate(db, r.symbol, r.strategy_id, now, risk.symbol_cooldown_hours)
                if dup:
                    near_miss.append((raw, r, dup))
                    continue
                final = round(raw * GRADE_FACTOR[ev["grade"]], 2)
                candidates.append((final, r, act.agent_id, v.sizing, r.reasons + v.reasons, ev, breakdown))
            t.result = {"summary": f"{found} setup(s)"}

    minimum = float((active_data(db, "daily_pick") or {}).get("min_score", DEFAULT_PICK_MIN_SCORE))
    candidates.sort(key=lambda c: c[0], reverse=True)
    with runtime.task(db, "manager", "daily_pick", f"Choosing today's best recommendation from {len(candidates)} candidate(s)") as t:
        if candidates and candidates[0][0] >= minimum:
            final, s, agent_id, sizing, reasons, ev, breakdown = candidates[0]
            ctx = contexts[s.symbol]
            ev_line = (f"Strategy evidence: grade {ev['grade']} — {ev['label']}" +
                       (f" ({ev['trades']} backtest trades, net {ev['expectancy_pct']:+.2f}%/trade)" if ev.get("expectancy_pct") is not None else ""))
            reasons = ["⭐ DAILY BEST PICK — best of all strategies today", ev_line] + reasons
            sig = pub.publish(db, s, ctx, risk, sizing, final, {**breakdown, "evidence_grade": ev["grade"]}, reasons, now, ref)
            sig.agent_outputs = {**sig.agent_outputs, "hq": {**sig.agent_outputs["hq"], "agent_id": agent_id, "daily_pick": True}}
            db.add(HQDecision(cycle_ref=ref, symbol=s.symbol, decision="SIGNAL", stage="publisher", reasons=reasons[:8],
                              strategy_id=s.strategy_id, score=final, signal_id=sig.id,
                              levels={"entry": s.entry, "stop": s.stop, "take_profit": s.take_profit}))
            db.commit()
            facts = {"pair": f"{sig.base_asset}/USDT", "entry": sig.entry_price, "take_profit": sig.take_profit,
                     "stop_loss": sig.stop_loss, "invalidation": sig.invalidation_price, "risk_pct": sig.risk_pct,
                     "score": final, "strategy": s.strategy_id, "evidence_grade": ev["grade"], "reasons": reasons[:6]}
            sig.narrative, _ = await llm.write_text(db, "manager", "Explain today's best pick in 3 short sentences (English) "
                                                    "then 2 in Arabic. Mention the evidence grade honestly. End with: Not financial advice.",
                                                    facts, "daily_pick", "; ".join(reasons[:3]) + ". Not financial advice.", 500)
            db.commit()
            bus.publish(db, "signal", "publisher", signal_id=sig.id, symbol=sig.symbol, score=final, strategy=s.strategy_id, daily_pick=True)
            t.result = {"summary": f"daily pick: {sig.symbol} via {s.strategy_id} (score {final})"}
            out = {"status": "picked", "signal_id": sig.id, "symbol": sig.symbol, "strategy": s.strategy_id, "score": final,
                   "evidence": ev, "alternatives": [{"symbol": c[1].symbol, "strategy": c[1].strategy_id, "score": c[0]} for c in candidates[1:4]]}
            if notify:
                await notify("new_signal", sig)
            return out
        # NO TRADE today + watchlist (not recommendations)
        near_miss.sort(key=lambda x: x[0], reverse=True)
        watch = [{"symbol": s.symbol, "strategy": s.strategy_id, "entry": s.entry, "why_not": why[:200]} for _, s, why in near_miss[:3]]
        if candidates:
            best = candidates[0]
            watch.insert(0, {"symbol": best[1].symbol, "strategy": best[1].strategy_id, "entry": best[1].entry,
                             "why_not": f"score {best[0]:.0f} below daily minimum {minimum:.0f}"})
        db.add(HQDecision(cycle_ref=ref, symbol="*", decision="NO_TRADE", stage="manager",
                          reasons=[f"NO TRADE today: no setup passed every rule ({len(contexts)} markets × all strategies)"]
                          + [f"watch {w['symbol']} ({w['strategy']}): {w['why_not']}" for w in watch]))
        db.commit()
        trig = triggers(contexts)
        btc_line = (f"- BTC 7 days: {btc.features['ret_168_1h']:+.2f}% · {btc_ok[1]}" if btc and "ret_168_1h" in btc.features
                    else f"- {btc_ok[1]}")
        lines = [f"# Daily pick {day}: NO TRADE", "",
                 f"All 8 strategies checked {len(contexts)} markets. No setup passed every rule, so there is no recommendation yet.",
                 btc_line, "I re-check at the next scheduled time today and publish automatically if a valid setup appears.", "",
                 "## Triggers to watch (conditions, NOT recommendations)",
                 *([f"- {x['symbol']} — {x['strategy']}: {x['trigger']} ({x['distance_pct']}% away)" for x in trig] or ["- none close"]),
                 "", "## Near misses", *([f"- {w['symbol']} — {w['strategy']}: {w['why_not']}" for w in watch] or ["- none"]),
                 "", "_Not financial advice._"]
        first_today = not db.scalar(select(HQReport).where(HQReport.kind == "daily_pick", HQReport.day == day))
        rep = save_report(db, "daily_pick", f"Daily pick {day}: NO TRADE", "\n".join(lines),
                          {"watchlist": watch, "triggers": trig}, "manager")
        t.result = {"summary": f"NO TRADE; {len(trig)} trigger(s) on watch"}
    bus.publish(db, "no_trade", "manager", cycle_ref=ref, reasons=[x["trigger"] for x in trig][:3])
    if first_today:  # one NO TRADE message per day; later re-checks stay silent unless a pick is published
        msg = md_to_telegram("\n".join(lines))
        if notify:
            await notify("text", msg)
        elif notify_owner:
            await notify_owner(msg)
    return {"status": "no_trade", "report_id": rep.id, "watchlist": watch, "triggers": trig}


def needs_tournament(db: Session, max_age_days: int = 8) -> bool:
    since = utcnow() - timedelta(days=max_age_days)
    return not db.scalar(select(HQBacktestRun).where(HQBacktestRun.subject.like("%@tournament"), HQBacktestRun.created_at >= since))
