"""Loop A — Trading: Scanner → Structure Analyst → Strategy agents → Risk (veto) → Manager → Publisher.

Every candidate ends as a persisted HQDecision: SIGNAL, or NO_TRADE with the stage and reasons.
The LLM is only called for signals that are actually published (explanation text), never per candle.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

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
from ..data import fetch_context
from ..models import HQDecision
from ..registry import ensure_agents
from ..strategies.base import Reject, Setup
from ..strategies.registry import active_strategies

log = logging.getLogger("cryptonex.hq.trading")
_lock = asyncio.Lock()


def _decision(db: Session, ref: str, symbol: str, decision: str, stage: str, reasons: list[str],
              strategy_id: str | None = None, levels: dict | None = None, score: float | None = None,
              signal_id: int | None = None) -> None:
    db.add(HQDecision(cycle_ref=ref, symbol=symbol, decision=decision, stage=stage, reasons=reasons[:12],
                      strategy_id=strategy_id, levels=levels or {}, score=score, signal_id=signal_id))


def _levels(s: Setup) -> dict:
    return {"entry": s.entry, "stop": s.stop, "take_profit": s.take_profit, "invalidation": s.invalidation,
            "net_rr": s.net_rr, "move_pct": s.move_pct, "stop_pct": s.stop_pct}


async def run_hq_cycle(db: Session, client: BinanceClient, notify=None, now: datetime | None = None) -> dict:
    if _lock.locked():
        return {"status": "skipped", "reason": "another HQ cycle is running"}
    async with _lock:
        return await _run(db, client, notify, now or utcnow())


async def _run(db: Session, client: BinanceClient, notify, now: datetime) -> dict:
    ensure_agents(db)
    ref = uuid.uuid4().hex[:10]
    cfg = load_config(db)
    risk = cfg.risk
    bus.publish(db, "cycle_started", "manager", cycle_ref=ref)
    if cfg.paused:
        _decision(db, ref, "*", "NO_TRADE", "manager", ["signal generation is paused by the owner"])
        db.commit()
        return {"status": "paused", "cycle_ref": ref, "signals": [], "no_trade": ["paused"]}

    # 1) Scanner — universe from liquidity / volatility, then spread filter
    with runtime.task(db, "scanner", "scan", "Building today's USDT universe from Binance Spot") as t:
        symbols = await client.exchange_info()
        tickers = await client.tickers_24h()
        scan = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=risk.min_quote_volume_usdt,
                                        excluded_bases=cfg.excluded_bases, shortlist_size=risk.shortlist_size))
        universe = []
        for c in scan.shortlist:
            try:
                bid, ask = await client.book_ticker(c.symbol)
            except MarketDataError as e:
                _decision(db, ref, c.symbol, "NO_TRADE", "scanner", [f"order book unavailable: {e}"])
                continue
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            if spread > risk.max_spread_pct:
                _decision(db, ref, c.symbol, "NO_TRADE", "scanner", [f"spread {spread:.3f}% > {risk.max_spread_pct}%"])
                continue
            universe.append(c.symbol)
        t.result = {"summary": f"{len(universe)} markets in universe from {scan.scanned} tickers", "universe": universe,
                    "excluded": scan.excluded}

    # 2) Structure Analyst — multi-timeframe context (4H trend, 1H confirmation, 15M entry) + BTC
    contexts = {}
    with runtime.task(db, "structure", "structure", f"Reading 4H/1H/15M structure for {len(universe)} markets") as t:
        try:
            btc = await fetch_context(client, "BTCUSDT", symbols.get("BTCUSDT"))
        except MarketDataError as e:
            btc = None
            log.warning("BTC context unavailable: %s", e)
        for sym in universe:
            try:
                contexts[sym] = btc if sym == "BTCUSDT" and btc else await fetch_context(client, sym, symbols.get(sym))
            except MarketDataError as e:
                _decision(db, ref, sym, "NO_TRADE", "structure", [f"market data unavailable/stale: {e}"])
        t.result = {"summary": f"structure ready for {len(contexts)} markets",
                    "bias": {s: {tf: c.structure.get(tf, {}).get("bias") for tf in ("4h", "1h", "15m")} for s, c in contexts.items()}}

    # 3) Strategy agents — independent plug-ins
    setups: list[tuple[Setup, str]] = []
    rejects: dict[str, list[str]] = {s: [] for s in contexts}
    for act in active_strategies(db):
        with runtime.task(db, act.agent_id, "strategy", f"{act.strategy.name}: checking {len(contexts)} markets") as t:
            found = 0
            for sym, ctx in contexts.items():
                r = act.strategy.evaluate(ctx, act.params, risk)
                if isinstance(r, Reject):
                    rejects[sym].append(f"{r.strategy_id}: {r.reason}")
                else:
                    setups.append((r, act.agent_id))
                    found += 1
            t.result = {"summary": f"{found} candidate setup(s)"}
    for sym, rs in rejects.items():
        if rs and not any(s.symbol == sym for s, _ in setups):
            _decision(db, ref, sym, "NO_TRADE", "strategy", rs)

    # 4) Risk Manager — veto power
    open_q = select(Signal).where(Signal.status.not_in([s.value for s in TERMINAL_STATUSES]))
    open_sigs = list(db.scalars(open_q))
    signals_today = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= utc_day_start(now))) or 0
    approved: list[tuple[Setup, str, dict, list[str]]] = []
    with runtime.task(db, "risk", "risk", f"Risk review of {len(setups)} setup(s)") as t:
        btc_ok = riskmod.btc_filter(btc)
        for s, agent_id in setups:
            v = riskmod.assess(s, contexts[s.symbol], risk, btc_ok, len(open_sigs), {x.symbol for x in open_sigs}, signals_today)
            if v.approved:
                approved.append((s, agent_id, v.sizing, s.reasons + v.reasons))
            else:
                _decision(db, ref, s.symbol, "NO_TRADE", "risk", ["RISK VETO"] + v.reasons, s.strategy_id, _levels(s))
                bus.publish(db, "veto", "risk", symbol=s.symbol, strategy=s.strategy_id, reasons=v.reasons[:3])
        t.result = {"summary": f"{len(approved)} approved, {len(setups) - len(approved)} vetoed", "btc_filter": btc_ok[1]}

    # 5) Manager — rank and keep within the daily quota (one per symbol)
    quota = max(risk.max_signals_per_day - signals_today, 0)
    with runtime.task(db, "manager", "select", f"Ranking {len(approved)} approved setup(s)") as t:
        ranked = sorted(approved, key=lambda x: (x[0].quality, x[0].net_rr), reverse=True)
        chosen, seen = [], set()
        for item in ranked:
            s = item[0]
            if s.symbol in seen:
                _decision(db, ref, s.symbol, "NO_TRADE", "manager", ["another strategy on this symbol ranked higher"], s.strategy_id, _levels(s))
                continue
            if len(chosen) >= quota:
                _decision(db, ref, s.symbol, "NO_TRADE", "manager", ["daily signal quota filled by higher-ranked setups"], s.strategy_id, _levels(s))
                continue
            seen.add(s.symbol)
            chosen.append(item)
        t.result = {"summary": f"{len(chosen)} selected for publishing"}

    # 6) Publisher — dedup, score, minimum score, publish
    published: list[Signal] = []
    with runtime.task(db, "publisher", "publish", f"Scoring {len(chosen)} setup(s)") as t:
        minimum = pub.min_score(db)
        for s, agent_id, sizing, reasons in chosen:
            dup = pub.is_duplicate(db, s.symbol, s.strategy_id, now, risk.symbol_cooldown_hours)
            if dup:
                _decision(db, ref, s.symbol, "NO_TRADE", "publisher", [dup], s.strategy_id, _levels(s))
                continue
            sc, breakdown = pub.score(s, contexts[s.symbol], risk, pub.agent_level(db, agent_id))
            if sc < minimum:
                _decision(db, ref, s.symbol, "NO_TRADE", "publisher", [f"score {sc:.0f} below minimum {minimum:.0f}"],
                          s.strategy_id, _levels(s), sc)
                continue
            sig = pub.publish(db, s, contexts[s.symbol], risk, sizing, sc, breakdown, reasons, now, ref)
            sig.agent_outputs = {**sig.agent_outputs, "hq": {**sig.agent_outputs["hq"], "agent_id": agent_id}}
            _decision(db, ref, s.symbol, "SIGNAL", "publisher", reasons[:6], s.strategy_id, _levels(s), sc, sig.id)
            db.commit()
            facts = {"pair": f"{sig.base_asset}/USDT", "entry": sig.entry_price, "take_profit": sig.take_profit,
                     "stop_loss": sig.stop_loss, "invalidation": sig.invalidation_price, "risk_pct": sig.risk_pct,
                     "score": sc, "strategy": s.strategy_id, "reasons": reasons[:6]}
            sig.narrative, _ = await llm.write_text(
                db, "publisher", "Explain this BUY signal in 3 short sentences (English), then one line in Arabic. "
                "End with: Not financial advice.", facts, "signal_explain",
                f"{s.strategy_id}: " + "; ".join(reasons[:3]) + ". Not financial advice.", 400)
            db.commit()
            published.append(sig)
            bus.publish(db, "signal", "publisher", signal_id=sig.id, symbol=sig.symbol, score=sc, strategy=s.strategy_id)
        t.result = {"summary": f"{len(published)} signal(s) published"}

    no_trade_reasons: list[str] = []
    if not published:
        counts = {}
        for d in db.scalars(select(HQDecision).where(HQDecision.cycle_ref == ref, HQDecision.decision == "NO_TRADE")):
            counts[d.stage] = counts.get(d.stage, 0) + 1
        no_trade_reasons = [f"{n} candidate(s) stopped at {stage}" for stage, n in sorted(counts.items(), key=lambda x: -x[1])]
        if not universe:
            no_trade_reasons.insert(0, "no market passed the liquidity/spread filters")
        _decision(db, ref, "*", "NO_TRADE", "manager", ["NO TRADE this cycle: no setup passed every agent"] + no_trade_reasons)
        bus.publish(db, "no_trade", "manager", cycle_ref=ref, reasons=no_trade_reasons[:5])
    db.commit()
    if notify:
        for sig in published:
            await notify("new_signal", sig)
    bus.publish(db, "cycle_done", "manager", cycle_ref=ref, signals=len(published), universe=len(universe), setups=len(setups))
    return {"status": "ok", "cycle_ref": ref, "universe": universe, "setups": len(setups), "approved": len(approved),
            "signals": [s.id for s in published], "no_trade": no_trade_reasons}
