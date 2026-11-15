from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import ServiceState, utcnow

SERVICES = ("scanner", "tracker", "binance", "telegram", "scheduler")


def set_service_state(db: Session, name: str, ok: bool, error: str | None = None, status: str | None = None) -> None:
    st = db.get(ServiceState, name)
    if st is None:
        st = ServiceState(name=name)
        db.add(st)
    if ok:
        st.status, st.last_ok_at = status or "ok", utcnow()
    else:
        st.status, st.last_error_at, st.last_error = status or "error", utcnow(), (error or "")[:2000]
    db.commit()
