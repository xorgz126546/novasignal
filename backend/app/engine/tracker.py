"""Tracks every open signal on 1m candles until TP, SL or expiry (theoretical + paper tracks)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market.binance import BinanceClient, MarketDataError, closed_candles
from ..models import Signal, SignalStatus, utcnow
from ..trading_config import load_config
from . import lifecycle
from .services import set_service_state

log = logging.getLogger("cryptonex.tracker")


async def track_open_signals(db: Session, client: BinanceClient, notify=None, now: datetime | None = None) -> int:
    now = now or utcnow()
    risk = load_config(db).risk
    open_sigs = list(db.scalars(select(Signal).where(Signal.theoretical_outcome.is_(None),
                                                     Signal.status != SignalStatus.CANCELLED.value)))
    resolved = 0
    errors = []
    for sig in open_sigs:
        start = sig.last_checked_at or sig.created_at
        start_ms = int(start.timestamp() * 1000) - 60_000
        try:
            candles = closed_candles(await client.klines_since(sig.symbol, "1m", start_ms, 1000),
                                     int(now.timestamp() * 1000))
        except MarketDataError as e:
            errors.append(f"{sig.symbol}: {e}")
            continue
        prev_status = sig.status
        o = lifecycle.evaluate_candles(sig, candles, now)
        if candles:
            sig.last_price = candles[-1].close
            # resume from the last processed candle next time (never skip unprocessed data)
            sig.last_checked_at = datetime.fromtimestamp(candles[-1].open_time / 1000, timezone.utc)
        if o:
            lifecycle.apply_outcome(db, sig, o, risk)
            resolved += 1
            db.commit()
            if notify:
                await notify("outcome", sig, prev_status=prev_status)
    db.commit()
    set_service_state(db, "tracker", ok=not errors, error="; ".join(errors[:5]) if errors else None,
                      status="degraded" if errors else None)
    return resolved
