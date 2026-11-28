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
from .regime import fmt_radar, strongest
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
            out.append({"symbol": sym, "strategy": "oversold_bounce", "distance_pct": round((lvl - px) / px * 100, 2), "level": lvl,
                        "trigger": f"1H close above {lvl:.6g} with RSI(1h) ≥ 35 (was oversold at {f['rsi_min6_1h']:.1f})"})
        if "donchian_high20_4h" in f and f.get("adx_4h", 0) >= 20 and f.get("ema21_4h", 0) > f.get("ema50_4h", 0):
            lvl = f["donchian_high20_4h"]
            out.append({"symbol": sym, "strategy": "donchian_breakout", "distance_pct": round((lvl - px) / px * 100, 2), "level": lvl,
                        "trigger": f"4H close above {lvl:.6g} (20-bar 4H high)"})
        if f.get("squeeze_recent_1h") and "donchian_high20_1h" in f:
            lvl = f["donchian_high20_1h"]
            out.append({"symbol": sym, "strategy": "squeeze_breakout", "distance_pct": round((lvl - px) / px * 100, 2), "level": lvl,
                        "trigger": f"1H close above {lvl:.6g} on 1.3x+ volume after a volatility squeeze"})
    return sorted([t for t in out if 0 <= t["distance_pct"] <= 10], key=lambda t: t["distance_pct"])[:5]


# ---------------- daily best pick ----------------
PROFILE_ORDER = ("swing", "momentum", "scalp", "invest")


async def run_daily_pick(db: Session, client: BinanceClient, notify=None, notify_owner=None, now=None) -> dict:
    """Best recommendation of the day PER TYPE (swing, scalp, long-term), each at most once per day."""
    now = now or utcnow()
    day = local_day(now)
    ensure_agents(db)
    cfg = load_config(db)
    risk = cfg.risk
    if cfg.paused:
        return {"status": "paused"}
    already = {d.cycle_ref.rsplit("-", 1)[-1]: d.signal_id for d in db.scalars(
        select(HQDecision).where(HQDecision.cycle_ref.like(f"pick-{day}-%"), HQDecision.decision == "SIGNAL"))}
    todo = [p for p in PROFILE_ORDER if p not in already]
    signals_today = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= utc_day_start(now))) or 0
    if signals_today >= risk.max_signals_per_day:
        return {"status": "daily_limit_reached" if todo else "already_picked", "picks": already}
    bus.publish(db, "cycle_started", "manager", cycle_ref=f"pick-{day}", kind="daily_pick")

    with runtime.task(db, "scanner", "scan", "Daily pick: scanning a wider USDT universe") as t:
        symbols = await client.exchange_info()
        tickers = await client.tickers_24h()
        scan = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=risk.min_quote_volume_usdt,
                                        excluded_bases=cfg.excluded_bases, shortlist_size=max(risk.shortlist_size, 25)))
        universe = [c.symbol for c in scan.shortlist]
        qv = {c.symbol: c.quote_volume for c in scan.shortlist}
        from .movers import hot_symbols

        tick = {x.get("symbol"): x for x in tickers}
        for sym in hot_symbols(db):  # Tariq's movers: strong coins outside the top-volume shortlist
            if sym not in universe and sym in symbols:
                universe.append(sym)
                qv[sym] = float(tick.get(sym, {}).get("quoteVolume") or 0)
        t.result = {"summary": f"{len(universe)} markets for the daily pick"}

    contexts = {}
    with runtime.task(db, "structure", "structure", f"Daily pick: 1D/4H/1H/15M structure for {len(universe)} markets") as t:
        try:
            btc = await fetch_context(client, "BTCUSDT", symbols.get("BTCUSDT"))
        except MarketDataError:
            btc = None
        for sym in universe:
            try:
                contexts[sym] = await fetch_context(client, sym, symbols.get(sym))
            except MarketDataError:
                continue
        for sym, c in contexts.items():
            c.extras["btc_ret_7d"] = btc.features.get("ret_168_1h") if btc else None
            c.extras["quote_volume_24h"] = qv.get(sym)
        t.result = {"summary": f"{len(contexts)} markets ready"}

    from ..profiles import PROFILES, prepare

    candidates: dict[str, list] = {p: [] for p in PROFILE_ORDER}
    near_miss = []
    open_sigs = list(db.scalars(select(Signal).where(Signal.status.not_in([s.value for s in TERMINAL_STATUSES]))))
    btc_ok = riskmod.btc_filter(btc)
    for act in active_strategies(db):
        prof = getattr(act.strategy, "profile", "swing")  # all types scan: extra picks may come from any type
        ev = evidence(db, act.strategy.id)
        with runtime.task(db, act.agent_id, "strategy", f"Daily pick: {act.strategy.name} ({ev['grade']})") as t:
            found = 0
            for sym, ctx in contexts.items():
                prisk = prepare(ctx, act.strategy, risk)
                if prisk is None:
                    continue
                r = act.strategy.evaluate(ctx, act.params, prisk)
                if isinstance(r, Reject):
                    continue
                found += 1
                v = riskmod.assess(r, ctx, prisk, btc_ok, len(open_sigs), {x.symbol for x in open_sigs}, signals_today,
                                   riskmod.is_decoupled(ctx, btc))
                raw, breakdown = pub.score(r, ctx, prisk, pub.agent_level(db, act.agent_id))
                if not v.approved:
                    near_miss.append((raw, r, "risk veto: " + "; ".join(v.reasons[-2:])))
                    continue
                if ev["grade"] == "F":
                    near_miss.append((raw, r, f"strategy excluded: {ev['label']}"))
                    continue
                dup = pub.is_duplicate(db, r.symbol, r.strategy_id, now, prisk.symbol_cooldown_hours)
                if dup:
                    near_miss.append((raw, r, dup))
                    continue
                final = round(raw * GRADE_FACTOR[ev["grade"]], 2)
                candidates[prof].append((final, r, act.agent_id, v.sizing, r.reasons + v.reasons, ev, breakdown, prisk))
            t.result = {"summary": f"{found} setup(s)"}

    minimum = float((active_data(db, "daily_pick") or {}).get("min_score", DEFAULT_PICK_MIN_SCORE))
    picks, quota = [], risk.max_signals_per_day - signals_today
    used_symbols = {x.symbol for x in open_sigs}
    async def _publish(prof: str, cand: tuple, ref: str, headline: str) -> None:
        nonlocal quota
        final, s, agent_id, sizing, reasons, ev, breakdown, prisk = cand
        label = PROFILES[prof]
        ctx = contexts[s.symbol]
        ev_line = (f"Strategy evidence: grade {ev['grade']} — {ev['label']}" +
                   (f" ({ev['trades']} backtest trades, net {ev['expectancy_pct']:+.2f}%/trade)" if ev.get("expectancy_pct") is not None else ""))
        reasons = [f"{headline} — {label.label_en} / {label.label_ar}", ev_line] + reasons
        sig = pub.publish(db, s, ctx, prisk, sizing, final, {**breakdown, "evidence_grade": ev["grade"]}, reasons, now, ref)
        sig.agent_outputs = {**sig.agent_outputs, "hq": {**sig.agent_outputs["hq"], "agent_id": agent_id, "daily_pick": True,
                                                         "profile": prof}}
        db.add(HQDecision(cycle_ref=ref, symbol=s.symbol, decision="SIGNAL", stage="publisher", reasons=reasons[:8],
                          strategy_id=s.strategy_id, score=final, signal_id=sig.id,
                          levels={"entry": s.entry, "stop": s.stop, "take_profit": s.take_profit}))
        db.commit()
        facts = {"type": label.label_en, "pair": f"{sig.base_asset}/USDT", "entry": sig.entry_price, "take_profit": sig.take_profit,
                 "stop_loss": sig.stop_loss, "invalidation": sig.invalidation_price, "risk_pct": sig.risk_pct,
                 "score": final, "strategy": s.strategy_id, "evidence_grade": ev["grade"], "reasons": reasons[:6]}
        sig.narrative, _ = await llm.write_text(db, "manager", "Explain this pick in 3 short sentences (English) then 2 in "
                                                "Arabic. Mention the trade type and the evidence grade honestly. End with: Not financial advice.",
                                                facts, "daily_pick", "; ".join(reasons[:3]) + ". Not financial advice.", 500)
        db.commit()
        bus.publish(db, "signal", "publisher", signal_id=sig.id, symbol=sig.symbol, score=final, strategy=s.strategy_id,
                    daily_pick=True, profile=prof)
        picks.append({"profile": prof, "signal_id": sig.id, "symbol": sig.symbol, "strategy": s.strategy_id, "score": final,
                      "evidence": ev, "ref": ref})
        used_symbols.add(s.symbol)
        quota -= 1
        if notify:
            await notify("new_signal", sig)

    with runtime.task(db, "manager", "daily_pick", "Choosing today's best recommendation per type") as t:
        for prof in todo:
            ranked = sorted([c for c in candidates[prof] if c[1].symbol not in used_symbols], key=lambda c: c[0], reverse=True)
            if not ranked or ranked[0][0] < minimum or quota <= 0:
                if ranked and ranked[0][0] < minimum:
                    near_miss.append((ranked[0][0], ranked[0][1], f"score {ranked[0][0]:.0f} below daily minimum {minimum:.0f}"))
                continue
            await _publish(prof, ranked[0], f"pick-{day}-{prof}", "⭐ DAILY BEST PICK")
        # Remaining daily quota (owner sets "Max signals / day", up to 5 recommended): next-best setups of ANY type that
        # pass exactly the same rules and minimum score. Never forced: no qualifying setup → no extra signal.
        n_extra = sum(1 for k in already if k.startswith("extra"))
        pool = sorted(((c, prof) for prof in PROFILE_ORDER for c in candidates[prof]), key=lambda x: x[0][0], reverse=True)
        for c, prof in pool:
            if quota <= 0:
                break
            if c[1].symbol in used_symbols or c[0] < minimum:
                continue
            n_extra += 1
            await _publish(prof, c, f"pick-{day}-extra{n_extra}", f"⭐ EXTRA PICK #{n_extra}")
        t.result = {"summary": f"{len(picks)} pick(s): " + ", ".join(f"{p['profile']} {p['symbol']}" for p in picks) or "none"}

    missing = [p for p in todo if p not in {x["profile"] for x in picks}]
    if not todo and not picks:
        return {"status": "already_picked", "picks": already}
    if picks and not missing:
        return {"status": "picked", "picks": picks, **picks[0]}
    near_miss.sort(key=lambda x: x[0], reverse=True)
    watch = [{"symbol": s.symbol, "strategy": s.strategy_id, "entry": s.entry, "why_not": why[:200]} for _, s, why in near_miss[:4]]
    trig = triggers(contexts)
    btc_line = (f"- BTC 7 days: {btc.features['ret_168_1h']:+.2f}% · {btc_ok[1]}" if btc and "ret_168_1h" in btc.features
                else f"- {btc_ok[1]}")
    names = {p: f"{PROFILES[p].label_en} / {PROFILES[p].label_ar}" for p in PROFILE_ORDER}
    lines = [f"# Daily pick {day}", "",
             *([f"- ✅ {names[p['profile']]}: {p['symbol']} ({p['strategy']})" for p in picks]),
             *([f"- ⏸ {names[p]}: NO TRADE — no setup passed every rule" for p in missing]),
             btc_line, "I re-check at the next scheduled time today and publish automatically if a valid setup appears.", "",
             "## Triggers to watch (conditions, NOT recommendations)",
             *([f"- {x['symbol']} — {x['strategy']}: {x['trigger']} ({x['distance_pct']}% away)" for x in trig] or ["- none close"]),
             "", "## Near misses", *([f"- {w['symbol']} — {w['strategy']}: {w['why_not']}" for w in watch] or ["- none"]),
             "", "## 💪 Strongest vs BTC, 7 days (watch for when the market turns — NOT recommendations)",
             *fmt_radar(strongest(contexts, btc.features.get("ret_168_1h") if btc else None)),
             "", "_Not financial advice._"]
    for p in missing:
        db.add(HQDecision(cycle_ref=f"pick-{day}-{p}", symbol="*", decision="NO_TRADE", stage="manager",
                          reasons=[f"NO TRADE ({p}): no setup passed every rule ({len(contexts)} markets)"]
                          + [f"watch {w['symbol']} ({w['strategy']}): {w['why_not']}" for w in watch[:3]]))
    db.commit()
    first_today = not db.scalar(select(HQReport).where(HQReport.kind == "daily_pick", HQReport.day == day))
    rep = save_report(db, "daily_pick", f"Daily pick {day}: {len(picks)} pick(s), NO TRADE for {', '.join(missing)}",
                      "\n".join(lines), {"picks": picks, "watchlist": watch, "triggers": trig}, "manager")
    bus.publish(db, "no_trade", "manager", cycle_ref=f"pick-{day}", reasons=[x["trigger"] for x in trig][:3])
    if first_today:  # one summary message per day; later re-checks stay silent unless a pick is published
        msg = md_to_telegram("\n".join(lines))
        if notify:
            await notify("text", msg)
        elif notify_owner:
            await notify_owner(msg)
    return {"status": "picked" if picks else "no_trade", "picks": picks, "missing": missing, "report_id": rep.id,
            "watchlist": watch, "triggers": trig, **(picks[0] if picks else {})}


def needs_tournament(db: Session, max_age_days: int = 8) -> bool:
    since = utcnow() - timedelta(days=max_age_days)
    return not db.scalar(select(HQBacktestRun).where(HQBacktestRun.subject.like("%@tournament"), HQBacktestRun.created_at >= since))
