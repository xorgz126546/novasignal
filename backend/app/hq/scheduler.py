"""HQ job scheduler (local timezone, default Africa/Algiers). Runs only when HQ_ENABLED=true.

Last-run times are persisted (app_settings 'hq_jobs') so restarts don't re-run or skip daily jobs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import AppSetting, utcnow
from .reports import tz

log = logging.getLogger("cryptonex.hq.scheduler")
DOW = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
_task: asyncio.Task | None = None


def is_due(schedule: str, last_run: datetime | None, now: datetime) -> bool:
    """schedule: 'every Nm' | 'daily HH:MM' | 'twice_daily HH:MM,HH:MM' | 'weekly DOW HH:MM' (local time)."""
    local = now.astimezone(tz())
    kind, _, arg = schedule.partition(" ")
    if kind == "every":
        minutes = int(arg.rstrip("m"))
        return last_run is None or now - last_run >= timedelta(minutes=minutes)
    times: list[tuple[int, int]] = []
    weekday = None
    if kind in ("daily", "twice_daily"):
        times = [tuple(map(int, t.split(":"))) for t in arg.split(",")]
    elif kind == "weekly":
        d, hm = arg.split()
        weekday, times = DOW[d.upper()], [tuple(map(int, hm.split(":")))]
    else:
        return False
    for hh, mm in times:
        slot = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if weekday is not None and local.weekday() != weekday:
            continue
        if local >= slot and (last_run is None or last_run.astimezone(tz()) < slot):
            return True
    return False


def _state(db: Session) -> dict:
    row = db.get(AppSetting, "hq_jobs")
    return dict(row.value) if row else {}


def _save(db: Session, st: dict) -> None:
    row = db.get(AppSetting, "hq_jobs")
    if row:
        row.value = st
    else:
        db.add(AppSetting(key="hq_jobs", value=st))
    db.commit()


def jobs() -> list[tuple[str, str]]:
    s = get_settings()
    return [
        ("trading_cycle", f"every {s.hq_scan_interval_minutes}m"),
        ("auditor", "every 10m"),
        ("agent_reports", f"daily {max(s.hq_brief_hour - 1, 0):02d}:30"),
        ("brief", f"daily {s.hq_brief_hour:02d}:00"),
        ("scout", "weekly MON 07:00"),
        ("trends", "twice_daily 09:00,18:00"),
        ("content", "daily 10:00"),
        ("backtest_baselines", "weekly FRI 06:00"),
        ("library", f"weekly {list(DOW)[s.hq_library_weekday]} 17:00"),
        ("pr_watch", "every 30m"),
        ("daily_pick", f"daily {s.hq_daily_pick_time}"),
        ("tournament", "weekly SUN 05:00"),
    ]


def active_jobs() -> list[tuple[str, str]]:
    s = get_settings()
    if s.hq_enabled:
        return jobs()
    return [j for j in jobs() if s.hq_daily_pick and j[0] in ("daily_pick", "tournament")]


async def run_job(name: str, db: Session) -> None:
    from ..market.binance import get_client
    from ..telegram.bot import notify
    from .notify import notify_owner

    client = get_client()
    if name == "trading_cycle":
        from .loops.trading import run_hq_cycle
        await run_hq_cycle(db, client, notify)
    elif name == "auditor":
        from .agents.auditor import run_auditor
        from .loops.repair import handle_new_incidents
        r = await run_auditor(db, client, notify_owner)
        await handle_new_incidents(db, r["incidents"])
    elif name == "agent_reports":
        from .reports import write_all_agent_reports
        await write_all_agent_reports(db)
    elif name == "brief":
        from .reports import build_brief
        await build_brief(db, send=True)
    elif name == "scout":
        from .loops.upgrade import scout_run
        await scout_run(db)
    elif name == "trends":
        from .loops.growth import run_trends
        await run_trends(db, client)
    elif name == "content":
        from .loops.growth import run_content
        await run_content(db)
    elif name == "backtest_baselines":
        from .agents.backtester import run_gate
        from .strategies.registry import active_strategies
        for act in active_strategies(db):
            await run_gate(db, client, act.strategy, act.params, f"strategy:{act.strategy.id}@weekly")
    elif name == "library":
        from .loops.library import run_library
        await run_library(db)
    elif name == "daily_pick":
        from .loops.daily_pick import needs_tournament, run_daily_pick, run_tournament
        if needs_tournament(db):
            await run_tournament(db, client, notify_owner)
        await run_daily_pick(db, client, notify, notify_owner)
    elif name == "tournament":
        from .loops.daily_pick import run_tournament
        await run_tournament(db, client, notify_owner)
    elif name == "pr_watch":
        from .loops.repair import watch_prs
        await watch_prs(db)


async def tick(now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    ran = []
    with SessionLocal() as db:
        st = _state(db)
    for name, sched in active_jobs():
        last = datetime.fromisoformat(st[name]) if st.get(name) else None
        if not is_due(sched, last, now):
            continue
        with SessionLocal() as db:
            st[name] = now.isoformat()
            _save(db, st)  # mark first: a crashing job doesn't loop every minute
            try:
                await run_job(name, db)
                ran.append(name)
            except Exception:
                log.exception("HQ job %s failed", name)
    return ran


async def _loop() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("HQ scheduler tick failed")
        await asyncio.sleep(60)


def start() -> None:
    global _task
    s = get_settings()
    if _task is None and (s.hq_enabled or s.hq_daily_pick):
        _task = asyncio.get_running_loop().create_task(_loop())
        log.info("HQ scheduler started (%s)", get_settings().hq_timezone)


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
