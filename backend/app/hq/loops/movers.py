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


IGNITION_KEY = "ignition_alerts"


async def _ignitions(db: Session, client: BinanceClient, scan) -> list[dict]:
    """Coins in a momentum ignition right now (+10…+40% in 3 days on ≥1.5× volume) — the first days of a move."""
    from ..strategies.momentum import MomentumIgnition, ignition_stats

    p = MomentumIgnition.default_params
    out = []
    for c in scan.shortlist:
        try:
            c4 = closed_candles(await client.klines(c.symbol, "4h", 30))
            d = closed_candles(await client.klines(c.symbol, "1d", 30))
        except MarketDataError:
            continue
        st = ignition_stats(c4, d)
        if st and p["ret3_min"] <= st[0] <= p["ret3_max"] and st[1] >= p["vol_min"]:
            out.append({"symbol": c.symbol, "ret_3d": round(st[0], 2), "vol_3d": round(st[1], 2), "ignition": True})
    return sorted(out, key=lambda x: x["vol_3d"], reverse=True)


async def hunt_movers(db: Session, client: BinanceClient, size: int = 8, notify_owner=None, on_ignition=None) -> list[dict]:
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
        from ..profiles import PROFILES

        ign_min = PROFILES["momentum"].overrides.get("min_quote_volume_usdt", risk.min_quote_volume_usdt)
        wide = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=ign_min,
                                        excluded_bases=cfg.excluded_bases, shortlist_size=60, max_change_pct=60.0))
        ign = await _ignitions(db, client, wide)
        by_sym = {h["symbol"]: h for h in hot}
        for x in ign[:size]:
            if x["symbol"] in by_sym:
                by_sym[x["symbol"]].update(x)  # already hot: mark it as an ignition too
            else:
                hot.append(x)
        new_ign = _new_ignitions(db, ign)
        row = db.get(AppSetting, KEY)
        val = {"at": utcnow().isoformat(), "symbols": hot}
        if row:
            row.value = val
        else:
            db.add(AppSetting(key=KEY, value=val))
        db.commit()
        t.result = {"summary": f"hot list: {', '.join(h['symbol'] for h in hot) or 'none'}"
                               + (f" · ignition: {', '.join(x['symbol'] for x in ign)}" if ign else "")}
    bus.publish(db, "hot_list", "trend_hunter", symbols=[h["symbol"] for h in hot])
    if new_ign:
        bus.publish(db, "ignition", "trend_hunter", symbols=[x["symbol"] for x in new_ign])
        if notify_owner:
            lines = ["🚀 <b>بداية زخم — قبل ما تولّي ترند</b>", ""]
            lines += [f"• {x['symbol']}: {x['ret_3d']:+.1f}% في 3 أيام، حجم {x['vol_3d']:.1f}× المعتاد" for x in new_ign]
            lines += ["", "الفريق يفحصها الآن بكل القواعد. هذه <b>مراقبة وليست توصية</b> — التوصية تصلك فقط إذا نجحت.",
                      "<i>ليست نصيحة مالية.</i>"]
            await notify_owner("\n".join(lines))
        if on_ignition:
            await on_ignition()
    return hot


def _new_ignitions(db: Session, ign: list[dict]) -> list[dict]:
    """Ignitions not alerted in the last 3 days (one heads-up per coin per move)."""
    from datetime import datetime

    row = db.get(AppSetting, IGNITION_KEY)
    seen = dict(row.value or {}) if row else {}
    now = utcnow()
    fresh = [x for x in ign if x["symbol"] not in seen or now - datetime.fromisoformat(seen[x["symbol"]]) > timedelta(days=3)]
    for x in fresh:
        seen[x["symbol"]] = now.isoformat()
    seen = {k: v for k, v in seen.items() if now - datetime.fromisoformat(v) <= timedelta(days=7)}
    if row:
        row.value = seen
    else:
        db.add(AppSetting(key=IGNITION_KEY, value=seen))
    db.commit()
    return fresh


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
