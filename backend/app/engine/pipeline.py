"""Signal-generation pipeline, coordinated by the Chief Trading Officer (Agent 11).

scan -> per-candidate analysis (agents 02-07) -> entry/exit (10) -> risk (08) -> validation (09)
-> chief decision (11) -> persist signal + paper trade + notifications. Every evaluated
opportunity, approved or rejected, is stored for the 30-day validation program.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents import (ChiefTradingOfficer, EntryExitEngine, MarketScanner, NewsSentiment, PriceActionAnalyst,
                      RiskManager, StrategyValidator, SupportResistance, TechnicalAnalyst, TrendDetector,
                      VolumeIntelligence)
from ..agents.a07_news_sentiment import cryptopanic_fetcher
from ..agents.a11_chief_trading_officer import Dossier
from ..agents.base import AgentOutput
from ..config import get_settings
from ..market.binance import BinanceClient, MarketDataError, assert_fresh, closed_candles
from ..models import (TERMINAL_STATUSES, AgentRun, Opportunity, ScanCycle, Signal, SignalEvent, SignalStatus,
                      utcnow)
from ..trading_config import TradingConfig, load_config
from . import lifecycle
from .services import set_service_state

log = logging.getLogger("cryptonex.pipeline")
SLIM_KEYS = {"indicators", "supports", "resistances", "items"}


def _slim(d: dict) -> dict:
    """Compact agent output for storage on the signal (full output stays in AgentRun)."""
    return {k: v for k, v in d.items() if k not in SLIM_KEYS} | (
        {"indicators": {tf: {k: v for k, v in x.items()} for tf, x in d["indicators"].items() if tf in ("1h", "4h")}}
        if "indicators" in d else {})


def make_recorder(db: Session, cycle_id: int | None):
    def rec(agent_id: str, symbol: str | None, dur: float, status: str, out: AgentOutput | None, err: str | None):
        db.add(AgentRun(cycle_id=cycle_id, agent_id=agent_id, symbol=symbol, duration_ms=dur, status=status,
                        passed=out.passed if out else None, score=out.score if out else None,
                        output=out.model_dump(mode="json") if out else {}, error=err))
    return rec


def utc_day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _past_signal_returns(db: Session) -> list[float]:
    return [r for r in db.scalars(select(Signal.theoretical_return_pct).where(Signal.theoretical_return_pct.is_not(None))
                                  .order_by(Signal.id.desc()).limit(100))]


_scan_lock = asyncio.Lock()


async def run_scan_cycle(db: Session, client: BinanceClient, notify=None, now: datetime | None = None) -> ScanCycle:
    """Serialized: a manual scan never overlaps a scheduled one (prevents double approvals)."""
    if _scan_lock.locked():
        c = ScanCycle(started_at=utcnow(), finished_at=utcnow(), status="skipped", notes="another scan is in progress")
        db.add(c)
        db.commit()
        return c
    async with _scan_lock:
        return await _run_scan_cycle(db, client, notify, now)


async def _run_scan_cycle(db: Session, client: BinanceClient, notify=None, now: datetime | None = None) -> ScanCycle:
    now = now or utcnow()
    cfg: TradingConfig = load_config(db)
    risk = cfg.risk
    cycle = ScanCycle(started_at=now)
    db.add(cycle)
    db.commit()
    rec = make_recorder(db, cycle.id)
    signals_today = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= utc_day_start(now))) or 0
    try:
        if cfg.paused:
            cycle.status, cycle.notes = "skipped", "signal generation paused"
            return cycle
        if signals_today >= risk.max_signals_per_day:
            cycle.status, cycle.notes = "skipped", f"daily limit reached ({signals_today}/{risk.max_signals_per_day})"
            return cycle

        symbols = await client.exchange_info()
        tickers = await client.tickers_24h()
        tick_by_sym = {t["symbol"]: t for t in tickers}
        scan = MarketScanner(rec).run(dict(tickers=tickers, symbols=symbols, min_quote_volume=risk.min_quote_volume_usdt,
                                           excluded_bases=cfg.excluded_bases, shortlist_size=risk.shortlist_size))
        cycle.candidates = len(scan.shortlist)

        open_sigs = list(db.scalars(select(Signal).where(Signal.status.not_in([s.value for s in TERMINAL_STATUSES]))))
        active_symbols = [s.symbol for s in open_sigs]
        cooldown = [s for s in db.scalars(select(Signal.symbol).where(
            Signal.created_at >= now - timedelta(hours=risk.symbol_cooldown_hours)))]
        past_returns = _past_signal_returns(db)
        s = get_settings()
        news = NewsSentiment(cryptopanic_fetcher(s.cryptopanic_api_key) if s.cryptopanic_api_key else None, rec)
        equity = 1000.0

        dossiers: list[Dossier] = []
        context: dict[str, dict] = {}
        for cand in scan.shortlist:
            sym = cand.symbol
            if sym in active_symbols:  # cheap pre-filter; chief also enforces
                dossiers.append(Dossier(symbol=sym, results={"chief_prefilter": {"passed": False}}))
                continue
            try:
                candles = {}
                for tf in cfg.indicators.timeframes:
                    cs = closed_candles(await client.klines(sym, tf, 300))
                    assert_fresh(cs, tf)
                    candles[tf] = cs
                bid, ask = await client.book_ticker(sym)
                last_price = await client.price(sym)
            except MarketDataError as e:
                log.warning("market data issue for %s: %s", sym, e)
                db.add(Opportunity(cycle_id=cycle.id, symbol=sym, decision="rejected",
                                   rejection_reasons=[f"market data unavailable/stale: {e}"]))
                continue
            snap = dict(symbol=sym, candles=candles, last_price=last_price)
            r: dict[str, dict] = {}
            ta = TechnicalAnalyst(rec).run(snap | {"config": cfg.indicators})
            r[ta.agent_id] = ta.model_dump(mode="json")
            r["trend_detector"] = TrendDetector(rec).run(snap | {"config": cfg.indicators}).model_dump(mode="json")
            r["volume_intelligence"] = VolumeIntelligence(rec).run(snap | dict(
                quote_volume_24h=float(tick_by_sym[sym]["quoteVolume"]), bid=bid, ask=ask,
                min_quote_volume=risk.min_quote_volume_usdt, max_spread_pct=risk.max_spread_pct)).model_dump(mode="json")
            sr = SupportResistance(rec).run(snap | dict(target_min_pct=risk.target_move_min_pct,
                                                        max_stop_distance_pct=risk.max_stop_distance_pct))
            r[sr.agent_id] = sr.model_dump(mode="json")
            r["price_action"] = PriceActionAnalyst(rec).run(snap).model_dump(mode="json")
            r["news_sentiment"] = (await news.arun(dict(symbol=sym, base_asset=cand.base))).model_dump(mode="json")
            p1h = ta.indicators.get("1h", {})
            info = symbols[sym]
            ee = EntryExitEngine(rec).run(dict(
                symbol=sym, last_price=last_price, atr_1h=p1h.get("atr") or 0.0, atr_4h=ta.indicators.get("4h", {}).get("atr"),
                invalidation_level=sr.invalidation_level, nearest_resistance=sr.nearest_resistance, tick_size=info.tick_size,
                target_min_pct=risk.target_move_min_pct, target_max_pct=risk.target_move_max_pct,
                min_stop_distance_pct=risk.min_stop_distance_pct, max_stop_distance_pct=risk.max_stop_distance_pct,
                fee_rate_pct=risk.fee_rate_pct, slippage_pct=risk.slippage_pct, min_risk_reward=risk.min_risk_reward))
            r[ee.agent_id] = ee.model_dump(mode="json")
            if ee.entry and ee.take_profit and ee.stop_loss:
                rm = RiskManager(rec).run(dict(
                    symbol=sym, entry=ee.entry, stop_loss=ee.stop_loss, take_profit=ee.take_profit, equity_usdt=equity,
                    risk_per_trade_pct=risk.risk_per_trade_pct, max_position_pct=risk.max_position_pct,
                    fee_rate_pct=risk.fee_rate_pct, slippage_pct=risk.slippage_pct, min_risk_reward=risk.min_risk_reward,
                    min_stop_distance_pct=risk.min_stop_distance_pct, max_stop_distance_pct=risk.max_stop_distance_pct,
                    step_size=info.step_size, min_notional=info.min_notional, open_user_trades=0,
                    max_open_user_trades=risk.max_open_user_trades, open_signals=len(open_sigs),
                    max_open_signals=risk.max_open_signals))
                r[rm.agent_id] = rm.model_dump(mode="json")
                tp_pct = ee.expected_move_pct or risk.target_move_min_pct
                sl_pct = ee.stop_distance_pct or risk.max_stop_distance_pct
            else:
                r["risk_manager"] = {"passed": False, "score": 0.0, "reasons": ["no valid levels from entry/exit engine"]}
                tp_pct, sl_pct = risk.target_move_min_pct, risk.max_stop_distance_pct
            r["strategy_validator"] = StrategyValidator(rec).run(dict(
                symbol=sym, agent_results=r, candles_1h=candles["1h"], tp_pct=tp_pct, sl_pct=sl_pct,
                round_trip_cost_pct=2 * (risk.fee_rate_pct + risk.slippage_pct), min_agreement=risk.min_agent_agreement,
                horizon_bars=int(risk.signal_horizon_hours), past_signal_returns=past_returns)).model_dump(mode="json")
            dossiers.append(Dossier(symbol=sym, results=r))
            context[sym] = {"info": info, "last_price": last_price}

        chief = ChiefTradingOfficer(rec).run(dict(
            dossiers=[d for d in dossiers if "chief_prefilter" not in d.results], paused=cfg.paused,
            signals_today=signals_today, max_signals_per_day=risk.max_signals_per_day, active_symbols=active_symbols,
            cooldown_symbols=cooldown, min_confidence=risk.min_confidence))
        new_signals: list[Signal] = []
        by_sym = {d.symbol: d for d in dossiers}
        for dec in chief.decisions:
            opp = Opportunity(cycle_id=cycle.id, symbol=dec.symbol, decision="approved" if dec.approved else "rejected",
                              rejection_reasons=dec.rejection_reasons, confidence=dec.confidence)
            db.add(opp)
            if not dec.approved:
                cycle.rejected += 1
                continue
            res = by_sym[dec.symbol].results
            ee, rm = res["entry_exit"], res["risk_manager"]
            if not all(ee.get(k) for k in ("entry", "take_profit", "stop_loss", "expected_move_pct")) or \
                    not (ee["stop_loss"] < ee["entry"] < ee["take_profit"]):
                opp.decision = "rejected"  # defence in depth: never publish incomplete levels
                opp.rejection_reasons = dec.rejection_reasons + ["incomplete or invalid entry/exit levels"]
                cycle.rejected += 1
                continue
            info = context[dec.symbol]["info"]
            sig = Signal(
                cycle_id=cycle.id, symbol=dec.symbol, base_asset=info.base, quote_asset=info.quote, direction="BUY",
                created_at=now, entry_price=ee["entry"], take_profit=ee["take_profit"], stop_loss=ee["stop_loss"],
                expected_move_pct=ee["expected_move_pct"], risk_reward=ee["risk_reward"],
                net_expected_return_pct=ee["net_expected_return_pct"], confidence=dec.confidence,
                confidence_breakdown=dec.breakdown, justification=dec.justification,
                agent_outputs={k: _slim(v) for k, v in res.items()},
                position_sizing={k: rm.get(k) for k in ("quantity", "notional_usdt", "risk_usdt", "reward_usdt",
                                                         "position_pct_of_equity", "net_risk_reward")}
                | {"reference_equity_usdt": equity, "risk_per_trade_pct": risk.risk_per_trade_pct},
                status=SignalStatus.ANALYZING.value, entry_valid_until=now + timedelta(hours=risk.entry_validity_hours),
                expires_at=now + timedelta(hours=risk.signal_horizon_hours), last_price=context[dec.symbol]["last_price"])
            db.add(sig)
            db.flush()
            db.add(SignalEvent(signal_id=sig.id, from_status=None, to_status=SignalStatus.ANALYZING.value, note="candidate"))
            lifecycle.transition(db, sig, SignalStatus.APPROVED, f"approved by CTO, confidence {dec.confidence:.1f}")
            try:
                from ..llm.narrative import generate_narrative

                sig.narrative = await generate_narrative({"symbol": sig.symbol, "entry": sig.entry_price,
                                                          "take_profit": sig.take_profit, "stop_loss": sig.stop_loss,
                                                          "justification": dec.justification})
            except Exception:
                log.exception("narrative failed")
            try:  # paper fill uses a FRESH price after approval (models execution delay)
                fill_ref = await client.price(sig.symbol)
            except MarketDataError:
                fill_ref = sig.entry_price
            lifecycle.open_paper_trade(db, sig, fill_ref, risk)
            lifecycle.transition(db, sig, SignalStatus.ACTIVE, "published; waiting for user decision")
            opp.signal_id = sig.id
            cycle.approved += 1
            new_signals.append(sig)
        cycle.status = "ok"
        cycle.notes = "; ".join(chief.reasons)
        db.commit()
        set_service_state(db, "scanner", ok=True)
        if notify:
            for sig in new_signals:
                await notify("new_signal", sig)
        return cycle
    except Exception as e:
        log.exception("scan cycle failed")
        db.rollback()
        cycle = db.get(ScanCycle, cycle.id)
        cycle.status, cycle.error = "error", f"{type(e).__name__}: {e}"
        set_service_state(db, "scanner", ok=False, error=cycle.error)
        if notify:
            await notify("system_alert", f"Scan cycle failed: {cycle.error}")
        return cycle
    finally:
        cycle.finished_at = utcnow()
        db.commit()
