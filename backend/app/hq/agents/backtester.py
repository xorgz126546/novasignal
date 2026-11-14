"""HQ Backtester agent: fetches real history (cached), runs the walk-forward gate, persists every run."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...market.backtest import GateThresholds, gate, simulate
from ...market.binance import BinanceClient, Candle, closed_candles
from ...models import utcnow
from ...trading_config import load_config
from .. import bus, runtime
from ..models import HQBacktestRun, HQCandleCache, HQProposal
from ..strategies.base import Strategy
from ..versions import active_data

DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
CACHE_TTL = timedelta(hours=6)


def thresholds(db: Session) -> GateThresholds:
    return GateThresholds(**(active_data(db, "backtest_gate") or {}))


INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}


async def _paged(client: BinanceClient, symbol: str, interval: str, limit: int) -> list[Candle]:
    """Up to `limit` most recent candles, fetched forward in pages of 1000 (Binance max per request)."""
    if limit <= 1000:
        return await client.klines(symbol, interval, limit)
    start = int(utcnow().timestamp() * 1000) - limit * INTERVAL_MS[interval]
    out: list[Candle] = []
    while len(out) < limit:
        page = await client.klines_since(symbol, interval, start, 1000)
        if not page:
            break
        out += [c for c in page if not out or c.open_time > out[-1].open_time]
        if len(page) < 1000:
            break
        start = page[-1].open_time + 1
    return out[-limit:]


async def history(db: Session, client: BinanceClient, symbol: str, interval: str, limit: int = 3000) -> list[Candle]:
    row = db.scalar(select(HQCandleCache).where(HQCandleCache.symbol == symbol, HQCandleCache.interval == interval))
    if row and row.fetched_at > utcnow() - CACHE_TTL and len(row.candles) >= min(limit, 300):
        return [Candle(*c) for c in row.candles][-limit:]
    cs = closed_candles(await _paged(client, symbol, interval, limit))
    data = [[c.open_time, c.open, c.high, c.low, c.close, c.volume, c.close_time, c.quote_volume, c.trades] for c in cs]
    if row:
        row.candles, row.fetched_at = data, utcnow()
    else:
        db.add(HQCandleCache(symbol=symbol, interval=interval, candles=data))
    db.commit()
    return cs


def variants_tried(db: Session, subject_prefix: str) -> int:
    """Count earlier gate runs for the same idea family — every attempt counts against overfitting."""
    return (db.scalar(select(func.count()).select_from(HQBacktestRun).where(HQBacktestRun.subject.startswith(subject_prefix))) or 0) + 1


async def run_gate(db: Session, client: BinanceClient, strategy: Strategy, params: dict, subject: str,
                   proposal: HQProposal | None = None, baseline: dict | None = None,
                   universe: list[str] | None = None) -> HQBacktestRun:
    risk = load_config(db).risk
    th = thresholds(db)
    with runtime.task(db, "backtester", "backtest", f"Walk-forward gate: {subject}") as t:
        data = {}
        for sym in universe or DEFAULT_UNIVERSE:
            data[sym] = (await history(db, client, sym, "1h", 3000), await history(db, client, sym, "4h", 1000))
        btc = data["BTCUSDT"][0] if "BTCUSDT" in data else await history(db, client, "BTCUSDT", "1h", 3000)
        res = await asyncio.to_thread(simulate, strategy, params, data, risk, None, 1, btc)
        n_var = variants_tried(db, subject.split("@")[0])
        result = gate(res, th, n_var, baseline)
        result["thresholds"] = asdict(th)
        run = HQBacktestRun(subject=subject, proposal_id=proposal.id if proposal else None, passed=result["passed"],
                            result=result)
        db.add(run)
        if proposal:
            proposal.gate, proposal.status = result, ("passed" if result["passed"] else "failed")
        db.commit()
        s = result["summary"]
        t.result = {"summary": f"{'PASSED' if result['passed'] else 'FAILED'}: {s['trades']} trades, "
                               f"expectancy {s['expectancy_pct'] if s['expectancy_pct'] is not None else 'n/a'}"}
    bus.publish(db, "backtest", "backtester", subject=subject, passed=result["passed"], trades=result["summary"]["trades"])
    if result["passed"] and proposal:
        from ..xp import award

        award(db, "backtester", "backtest_passed", f"run:{run.id}", note=subject)
        if proposal.agent_id != "backtester":
            award(db, proposal.agent_id, "backtest_passed", f"run:{run.id}", note=subject)
    return run
