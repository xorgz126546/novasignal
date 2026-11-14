"""Task runtime: agent status (idle/working/blocked), current task text, persisted tasks and handover events."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import Session

from ..models import utcnow
from . import bus
from .models import HQAgent, HQTask


def _agent(db: Session, agent_id: str) -> HQAgent:
    row = db.get(HQAgent, agent_id)
    if row is None:
        row = HQAgent(id=agent_id)
        db.add(row)
    return row


def set_status(db: Session, agent_id: str, status: str, task: str = "") -> None:
    row = _agent(db, agent_id)
    row.status, row.current_task, row.last_active_at = status, task[:500], utcnow()
    db.commit()
    bus.publish(db, "agent_status", agent_id, status=status, task=task[:500])


@contextmanager
def task(db: Session, agent_id: str, kind: str, title: str, parent_id: int | None = None, handover: bool = True):
    """Run a unit of agent work. Marks the agent working, persists the task, and on success emits a
    'handover' event (the office animates the agent walking to the Manager)."""
    t = HQTask(agent_id=agent_id, kind=kind, title=title[:1000], parent_id=parent_id)
    db.add(t)
    db.commit()
    set_status(db, agent_id, "working", title)
    bus.publish(db, "task_started", agent_id, task_id=t.id, kind=kind, title=title[:300])
    try:
        yield t
    except Exception as e:
        db.rollback()
        t = db.get(HQTask, t.id)
        t.status, t.error, t.finished_at = "failed", f"{type(e).__name__}: {e}"[:2000], utcnow()
        db.commit()
        set_status(db, agent_id, "blocked", f"failed: {title}"[:500])
        bus.publish(db, "task_failed", agent_id, task_id=t.id, kind=kind, error=t.error)
        raise
    else:
        t.status, t.finished_at = "done", utcnow()
        db.commit()
        bus.publish(db, "task_done", agent_id, task_id=t.id, kind=kind, title=title[:300],
                    summary=str((t.result or {}).get("summary", ""))[:300])
        if handover and agent_id != "manager":
            bus.publish(db, "handover", agent_id, to="manager", task_id=t.id)
        set_status(db, agent_id, "idle", "")
