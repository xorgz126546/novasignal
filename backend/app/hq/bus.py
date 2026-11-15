"""Internal event bus: every event is persisted (hq_events) and fanned out live to SSE subscribers.

publish() is safe to call from sync code running in worker threads and from async code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime

from sqlalchemy.orm import Session

from .models import HQEvent

log = logging.getLogger("cryptonex.hq.bus")
_subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
_lock = threading.Lock()


def _serialize(ev: HQEvent) -> dict:
    return {"id": ev.id, "at": ev.at.isoformat() if isinstance(ev.at, datetime) else ev.at, "type": ev.type,
            "agent_id": ev.agent_id, "data": ev.data}


def publish(db: Session, type_: str, agent_id: str | None = None, **data) -> dict:
    ev = HQEvent(type=type_, agent_id=agent_id, data=json.loads(json.dumps(data, default=str)))
    db.add(ev)
    db.commit()
    payload = _serialize(ev)
    with _lock:
        subs = list(_subs)
    for loop, q in subs:
        try:
            loop.call_soon_threadsafe(_put, q, payload)
        except RuntimeError:  # loop closed
            pass
    return payload


def _put(q: asyncio.Queue, payload: dict) -> None:
    if q.full():
        try:
            q.get_nowait()  # drop oldest for slow clients
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(payload)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    with _lock:
        _subs.append((asyncio.get_running_loop(), q))
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subs[:] = [(l, s) for l, s in _subs if s is not q]


def subscriber_count() -> int:
    return len(_subs)
