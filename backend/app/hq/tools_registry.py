"""Owner-approved analysis tools. A tool is unlocked only by an approved `tool:<name>` version (rollback-able)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..market.tools import TOOLS
from .versions import active_by_prefix


def unlocked(db: Session) -> set[str]:
    return {v.key.split(":", 1)[1] for v in active_by_prefix(db, "tool:")} & set(TOOLS)


def status(db: Session) -> list[dict]:
    on = unlocked(db)
    return [{"id": k, "en": t["en"], "ar": t["ar"], "unlocked": k in on, "features": list(t["features"])} for k, t in TOOLS.items()]
