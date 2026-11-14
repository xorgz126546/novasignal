"""Market-data access for HQ agents (Binance public data only, via the existing validated client)."""
from __future__ import annotations

from ..market.binance import BinanceClient, SymbolInfo, assert_fresh, closed_candles
from .features import Context, build_features

TF_LIMITS = {"4h": 300, "1h": 300, "15m": 200}


async def fetch_context(client: BinanceClient, symbol: str, info: SymbolInfo | None) -> Context:
    candles = {}
    for tf, limit in TF_LIMITS.items():
        cs = closed_candles(await client.klines(symbol, tf, limit))
        assert_fresh(cs, tf)
        candles[tf] = cs
    feats, structure = build_features(candles)
    return Context(symbol=symbol, candles=candles, features=feats, structure=structure,
                   last_price=candles["1h"][-1].close, tick_size=info.tick_size if info else 0.0,
                   step_size=info.step_size if info else 0.0, min_notional=info.min_notional if info else 5.0)
