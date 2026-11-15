"""Versioned live configuration with one-click rollback.

Keys: "strategy:<id>" (params/enabled), "rule_strategy:<slug>" (declarative rules), "prompt:<agent>",
"skills:<agent>", "publisher" (min score, …). Only approve()/rollback() may activate a version.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import HQConfigVersion


def active(db: Session, key: str) -> HQConfigVersion | None:
    return db.scalar(select(HQConfigVersion).where(HQConfigVersion.key == key, HQConfigVersion.active.is_(True)))


def active_data(db: Session, key: str) -> dict | None:
    v = active(db, key)
    return dict(v.data) if v else None


def active_by_prefix(db: Session, prefix: str) -> list[HQConfigVersion]:
    return list(db.scalars(select(HQConfigVersion).where(HQConfigVersion.key.startswith(prefix),
                                                         HQConfigVersion.active.is_(True))))


def history(db: Session, key: str) -> list[HQConfigVersion]:
    return list(db.scalars(select(HQConfigVersion).where(HQConfigVersion.key == key).order_by(HQConfigVersion.version.desc())))


def create_candidate(db: Session, key: str, data: dict, created_by: str, note: str = "") -> HQConfigVersion:
    """A new INACTIVE version. It changes nothing until activated through an approval."""
    last = db.scalar(select(HQConfigVersion.version).where(HQConfigVersion.key == key).order_by(HQConfigVersion.version.desc()))
    v = HQConfigVersion(key=key, version=(last or 0) + 1, data=data, active=False, created_by=created_by, note=note)
    db.add(v)
    db.flush()
    return v


def _activate(db: Session, v: HQConfigVersion) -> None:
    for other in db.scalars(select(HQConfigVersion).where(HQConfigVersion.key == v.key, HQConfigVersion.active.is_(True))):
        other.active = False
    v.active = True


def activate_version(db: Session, version_id: int, approval_id: int) -> HQConfigVersion:
    """Called ONLY by approvals.approve (owner decision)."""
    v = db.get(HQConfigVersion, version_id)
    if v is None:
        raise ValueError("version not found")
    _activate(db, v)
    v.approval_id = approval_id
    return v


def rollback(db: Session, key: str) -> HQConfigVersion | None:
    """Re-activate the previous version (or deactivate the key entirely if there is none). Owner action."""
    hist = history(db, key)
    cur = next((h for h in hist if h.active), None)
    if cur is None:
        raise ValueError("nothing active to roll back")
    older = [h for h in hist if h.version < cur.version and h.approval_id is not None]
    cur.active = False
    if older:
        older[0].active = True
        db.commit()
        return older[0]
    db.commit()
    return None
