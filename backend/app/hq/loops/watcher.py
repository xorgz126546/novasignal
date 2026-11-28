"""Celine — Setup Watcher (was the Content Editor; content is paused by the owner).

Every 15 minutes she checks the live price against the exact trigger levels from the latest daily pick report
(e.g. "4H close above 2,531"). When a level is crossed, the team re-checks immediately instead of waiting for the
next scheduled time, so a valid setup is published as soon as it exists. Crossing a level is NOT a signal: the
re-check still applies every rule. Each trigger fires at most once every 2 hours.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient, MarketDataError
from ...models import AppSetting, utcnow
from .. import bus, runtime
from ..models import HQReport

KEY = "trigger_watch"
COOLDOWN = timedelta(hours=2)


async def watch_triggers(db: Session, client: BinanceClient, on_trigger=None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    rep = db.scalar(select(HQReport).where(HQReport.kind == "daily_pick").order_by(HQReport.id.desc()).limit(1))
    trig = [x for x in ((rep.data or {}).get("triggers") or []) if x.get("level")] if rep else []
    row = db.get(AppSetting, KEY)
    fired = dict((row.value or {}).get("fired", {})) if row else {}
    hits = []
    with runtime.task(db, "content", "watch", f"Watching {len(trig)} trigger level(s) on live prices") as t:
        for x in trig:
            k = f"{x['symbol']}:{x['strategy']}:{x['level']}"
            if k in fired and now - datetime.fromisoformat(fired[k]) < COOLDOWN:
                continue
            try:
                px = await client.price(x["symbol"])
            except MarketDataError:
                continue
            if px >= x["level"]:
                fired[k] = now.isoformat()
                hits.append({**x, "price": px})
        val = {"fired": {k: v for k, v in fired.items() if now - datetime.fromisoformat(v) < timedelta(days=2)}}
        if row:
            row.value = val
        else:
            db.add(AppSetting(key=KEY, value=val))
        db.commit()
        t.result = {"summary": (f"{len(hits)} trigger(s) crossed: " + ", ".join(h["symbol"] for h in hits)) if hits
                    else f"no trigger crossed ({len(trig)} watched)"}
    if hits:
        bus.publish(db, "trigger_hit", "content", symbols=[h["symbol"] for h in hits])
        if on_trigger:
            await on_trigger()
    return {"watched": len(trig), "hits": hits}
