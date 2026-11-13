"""In-process asyncio scheduler: scan cycles, signal tracking, daily summary.

Run exactly ONE backend instance with ENABLE_SCHEDULER=true (or a dedicated worker) to avoid
duplicate scans. Each job opens its own DB session and never crashes the loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from .config import get_settings
from .db import SessionLocal
from .engine.pipeline import run_scan_cycle
from .engine.services import set_service_state
from .engine.tracker import track_open_signals
from .market.binance import get_client
from .models import utcnow

log = logging.getLogger("cryptonex.scheduler")
_tasks: list[asyncio.Task] = []


async def _every(seconds: float, name: str, fn):
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # keep running; record the failure
            log.exception("job %s failed", name)
            with SessionLocal() as db:
                set_service_state(db, name, ok=False, error=f"{type(e).__name__}: {e}")
        await asyncio.sleep(seconds)


async def _scan():
    from .telegram.bot import notify

    with SessionLocal() as db:
        await run_scan_cycle(db, get_client(), notify)


async def _track():
    from .telegram.bot import notify

    with SessionLocal() as db:
        await track_open_signals(db, get_client(), notify)


async def _daily_summary_loop():
    from .telegram.bot import notify

    s = get_settings()
    while True:
        now = utcnow()
        nxt = now.replace(hour=s.daily_summary_hour_utc, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            from sqlalchemy import func, select

            from .models import Signal
            with SessionLocal() as db:
                n = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= nxt - timedelta(days=1))) or 0
            await notify("daily_summary", f"<b>CRYPTONEX AI — daily summary</b>\nSignals in the last 24h: {n}")
        except Exception:
            log.exception("daily summary failed")


def start() -> None:
    s = get_settings()
    if _tasks:
        return
    _tasks.append(asyncio.create_task(_every(s.scan_interval_minutes * 60, "scanner", _scan)))
    _tasks.append(asyncio.create_task(_every(s.tracker_interval_seconds, "tracker", _track)))
    _tasks.append(asyncio.create_task(_daily_summary_loop()))
    with SessionLocal() as db:
        set_service_state(db, "scheduler", ok=True)
    log.info("scheduler started")


async def stop() -> None:
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()


async def restart() -> None:
    await stop()
    if get_settings().enable_scheduler:
        start()


def running() -> bool:
    return any(not t.done() for t in _tasks)
