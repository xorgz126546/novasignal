"""Loop G — Market regime watch + strongest-coins radar.

Every 30 minutes the Risk Manager re-checks the BTC market filter on real data. When the market turns from
risk-off to risk-on, the owner is contacted immediately with the coins that held up best (relative strength vs BTC
over 7 days, in their own uptrend) and the daily pick re-checks at once. This is a heads-up, NOT a recommendation:
every signal still has to pass every rule.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ...agents import MarketScanner
from ...market.binance import BinanceClient, MarketDataError, closed_candles
from ...models import AppSetting, utcnow
from .. import bus, runtime
from ..agents import risk as riskmod
from ..data import fetch_context
from ..features import build_features

log = logging.getLogger("cryptonex.hq.regime")
STATE_KEY = "market_regime"


def strongest(contexts: dict, btc_ret: float | None, n: int = 5) -> list[dict]:
    """Coins beating BTC over 7 days, ranked; flags whether each is in its own 4H uptrend."""
    rows = []
    for sym, c in contexts.items():
        r = c.features.get("ret_168_1h")
        if r is None or sym == "BTCUSDT":
            continue
        rel = r - (btc_ret or 0.0)
        up = c.features.get("close_4h", 0) > c.features.get("ema50_4h", float("inf")) if "close_4h" in c.features else None
        rows.append({"symbol": sym, "ret_7d": round(r, 2), "vs_btc": round(rel, 2), "uptrend_4h": up})
    rows.sort(key=lambda x: x["vs_btc"], reverse=True)
    return [x for x in rows if x["vs_btc"] > 0][:n]


async def _radar(client: BinanceClient, btc_ret: float | None, n: int = 5) -> list[dict]:
    tickers = await client.tickers_24h()
    symbols = await client.exchange_info()
    scan = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=20_000_000, excluded_bases=[],
                                    shortlist_size=25))
    ctxs = {}
    for cand in scan.shortlist:
        try:
            cs = {"1h": closed_candles(await client.klines(cand.symbol, "1h", 300)),
                  "4h": closed_candles(await client.klines(cand.symbol, "4h", 300))}
        except MarketDataError:
            continue
        f, st = build_features(cs, frozenset())
        from ..features import Context

        ctxs[cand.symbol] = Context(cand.symbol, cs, f, st, cs["1h"][-1].close if cs["1h"] else 0.0)
    return strongest(ctxs, btc_ret, n)


def fmt_radar(rows: list[dict]) -> list[str]:
    return [f"- {r['symbol']}: {r['ret_7d']:+.2f}% in 7 days ({r['vs_btc']:+.2f}% vs BTC)"
            f"{' · own 4H uptrend' if r.get('uptrend_4h') else ''}" for r in rows] or ["- none beating BTC right now"]


async def regime_watch(db: Session, client: BinanceClient, notify_owner=None, on_risk_on=None) -> dict:
    with runtime.task(db, "risk", "regime", "Market regime watch (BTC filter on live data)") as t:
        try:
            btc = await fetch_context(client, "BTCUSDT", None)
        except MarketDataError as e:
            t.result = {"summary": f"BTC data unavailable: {e}"}
            return {"status": "no_data"}
        ok, why = riskmod.btc_filter(btc)
        regime = "risk_on" if ok else "risk_off"
        row = db.get(AppSetting, STATE_KEY)
        prev = (row.value or {}).get("regime") if row else None
        btc_ret = btc.features.get("ret_168_1h")
        state = {"regime": regime, "why": why, "btc_7d": btc_ret, "checked_at": utcnow().isoformat(),
                 "since": (row.value or {}).get("since") if row and prev == regime else utcnow().isoformat()}
        if row:
            row.value = state
        else:
            db.add(AppSetting(key=STATE_KEY, value=state))
        db.commit()
        t.result = {"summary": f"{regime} ({why})"}
    changed = prev is not None and prev != regime
    out = {"status": "changed" if changed else "same", "regime": regime, "previous": prev, "why": why}
    if not changed:
        return out
    bus.publish(db, "regime", "risk", regime=regime, previous=prev, why=why)
    if regime == "risk_on":
        try:
            radar = await _radar(client, btc_ret)
        except MarketDataError:
            radar = []
        out["radar"] = radar
        msg = "\n".join([
            "🚀 <b>السوق تحوّل إلى إيجابي (risk-on)</b>", f"BTC 7 أيام: {btc_ret:+.2f}%" if btc_ret is not None else "",
            "فلتر BTC سمح من جديد بصفقات الشراء. الفريق يعيد فحص الفرص الآن.", "",
            "<b>💪 أقوى العملات مقابل BTC (للمراقبة، ليست توصيات):</b>", *fmt_radar(radar), "",
            "أي توصية ستصلك فقط إذا نجحت في كل القواعد. <i>ليست نصيحة مالية.</i>"])
        if notify_owner:
            await notify_owner(msg)
        if on_risk_on:
            await on_risk_on()
    elif notify_owner:
        await notify_owner(f"🛑 <b>السوق تحوّل إلى سلبي (risk-off)</b>\n{why}\nلا صفقات شراء جديدة إلا للعملات القائدة "
                           "المستقلة عن BTC وبنصف المخاطرة. <i>ليست نصيحة مالية.</i>")
    return out
