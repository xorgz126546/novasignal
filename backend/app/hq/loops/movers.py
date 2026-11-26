"""Tariq — Market Movers Hunter (was the content Trend Hunter; content is paused by the owner).

Every 30 minutes: finds liquid USDT coins that are moving UP with real volume right now (24h change +2…+15%, 1H volume
above normal, price above its 1H EMA21) and puts them on a short "hot list". The daily pick and the trading cycle add
the hot list to their universe, so strong coins outside the top-volume shortlist are not missed. The hot list is a
place to LOOK, never a recommendation: every signal still has to pass every rule (one TP, stop bounds, net R:R,
BTC filter / decoupled-leader half risk).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from ...agents import MarketScanner
from ...market.binance import BinanceClient, MarketDataError, closed_candles
from ...models import AppSetting, utcnow
from ...trading_config import load_config
from .. import bus, runtime
from ..features import build_features

KEY = "hot_list"
MAX_AGE = timedelta(hours=2)


async def hunt_movers(db: Session, client: BinanceClient, size: int = 8) -> list[dict]:
    cfg = load_config(db)
    risk = cfg.risk
    with runtime.task(db, "trend_hunter", "movers", "Hunting coins moving up with real volume") as t:
        symbols = await client.exchange_info()
        tickers = await client.tickers_24h()
        scan = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=risk.min_quote_volume_usdt,
                                        excluded_bases=cfg.excluded_bases, shortlist_size=50))
        pool = sorted([c for c in scan.shortlist if 2.0 <= c.change_pct <= 15.0], key=lambda c: c.change_pct, reverse=True)[:20]
        hot = []
        for c in pool:
            try:
                cs = closed_candles(await client.klines(c.symbol, "1h", 120))
            except MarketDataError:
                continue
            f, _ = build_features({"1h": cs}, frozenset())
            vol, rsi = f.get("vol_rel_1h", 0), f.get("rsi_1h", 50)
            if vol >= 1.2 and f.get("close_1h", 0) > f.get("ema21_1h", float("inf")) and rsi <= 75:
                hot.append({"symbol": c.symbol, "change_24h": round(c.change_pct, 2), "vol_rel_1h": round(vol, 2),
                            "rsi_1h": round(rsi, 1), "quote_volume": round(c.quote_volume)})
            if len(hot) >= size:
                break
        row = db.get(AppSetting, KEY)
        val = {"at": utcnow().isoformat(), "symbols": hot}
        if row:
            row.value = val
        else:
            db.add(AppSetting(key=KEY, value=val))
        db.commit()
        t.result = {"summary": f"hot list: {', '.join(h['symbol'] for h in hot) or 'none'}"}
    bus.publish(db, "hot_list", "trend_hunter", symbols=[h["symbol"] for h in hot])
    return hot


def hot_symbols(db: Session) -> list[str]:
    """Fresh hot list (≤ 2 hours old), or empty."""
    row = db.get(AppSetting, KEY)
    if not row or not row.value:
        return []
    from datetime import datetime

    try:
        at = datetime.fromisoformat(row.value["at"])
    except (KeyError, ValueError):
        return []
    if utcnow() - at > MAX_AGE:
        return []
    return [h["symbol"] for h in row.value.get("symbols", [])]
