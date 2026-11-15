from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.reports import build_report, program_window, to_markdown
from ..models import User, ValidationProgram, utcnow
from ..security import audit, require_admin, require_subscriber

router = APIRouter(prefix="/api/validation", tags=["validation"])


def _out(p: ValidationProgram) -> dict:
    now = utcnow()
    return {"id": p.id, "name": p.name, "started_at": p.started_at, "ends_at": p.ends_at, "status": p.status,
            "notes": p.notes, "day": min(30, max(1, (now - p.started_at).days + 1)),
            "days_total": (p.ends_at - p.started_at).days}


@router.get("")
def list_programs(user: User = Depends(require_subscriber), db: Session = Depends(get_db)):
    progs = list(db.scalars(select(ValidationProgram).order_by(ValidationProgram.id.desc())))
    for p in progs:
        if p.status == "running" and utcnow() >= p.ends_at:
            p.status = "completed"
    db.commit()
    return [_out(p) for p in progs]


class StartIn(BaseModel):
    name: str = Field("30-day validation", max_length=120)
    days: int = Field(30, ge=1, le=120)
    notes: str = Field("", max_length=2000)


@router.post("")
def start_program(body: StartIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(ValidationProgram).where(ValidationProgram.status == "running")):
        raise HTTPException(409, "A validation program is already running")
    now = utcnow()
    p = ValidationProgram(name=body.name, started_at=now, ends_at=now + timedelta(days=body.days), notes=body.notes)
    db.add(p)
    db.commit()
    audit(db, f"user:{admin.id}", "validation_started", program_id=p.id, days=body.days)
    return _out(p)


@router.post("/{pid}/abort")
def abort(pid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(ValidationProgram, pid)
    if not p:
        raise HTTPException(404)
    p.status = "aborted"
    db.commit()
    audit(db, f"user:{admin.id}", "validation_aborted", program_id=pid)
    return _out(p)


@router.get("/{pid}/report")
def report(pid: int, kind: str = Query("daily", pattern="^(daily|weekly|final)$"), date: str | None = None,
           format: str = Query("json", pattern="^(json|md)$"), user: User = Depends(require_subscriber),
           db: Session = Depends(get_db)):
    p = db.get(ValidationProgram, pid)
    if not p:
        raise HTTPException(404)
    day = None
    if date:
        try:
            day = datetime.fromisoformat(date).replace(tzinfo=p.started_at.tzinfo)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
    start, end = program_window(p, kind, day)
    rep = build_report(db, start, end, kind, user.id)
    rep["program"] = _out(p)
    if format == "md":
        return PlainTextResponse(to_markdown(rep), media_type="text/markdown")
    return rep
