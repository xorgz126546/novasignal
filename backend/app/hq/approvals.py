"""'Needs you' — the approval gate. Agents can only CREATE pending approvals; only the owner decides.

Approving a change activates its versioned config (with rollback). Approving a fix PR only records the
decision: the merge itself happens on GitHub by the owner (the app never merges, never touches main).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import utcnow
from . import bus, versions
from .models import HQApproval, HQDraft, HQProposal

KINDS = {"config", "rule_strategy", "prompt", "skills", "fix_pr", "content"}


class ApprovalError(ValueError):
    pass


def request(db: Session, kind: str, title: str, summary: str, payload: dict, agents: list[str],
            ref: str | None = None) -> HQApproval:
    if kind not in KINDS:
        raise ApprovalError(f"unknown approval kind {kind}")
    if kind in ("config", "rule_strategy", "prompt", "skills") and "version_id" not in payload:
        raise ApprovalError("config-type approvals must reference an inactive candidate version")
    if ref:
        existing = db.scalar(select(HQApproval).where(HQApproval.ref == ref))
        if existing:
            return existing
    a = HQApproval(kind=kind, title=title[:500], summary=summary, payload=payload, agents=agents, ref=ref)
    db.add(a)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(HQApproval).where(HQApproval.ref == ref))
    bus.publish(db, "needs_you", "manager", approval_id=a.id, kind=kind, title=a.title)
    return a


def pending(db: Session) -> list[HQApproval]:
    return list(db.scalars(select(HQApproval).where(HQApproval.status == "pending").order_by(HQApproval.id)))


def approve(db: Session, approval_id: int, actor: str) -> HQApproval:
    a = db.get(HQApproval, approval_id)
    if a is None:
        raise ApprovalError("approval not found")
    if a.status != "pending":
        raise ApprovalError(f"already {a.status}")
    if a.kind in ("config", "rule_strategy", "prompt", "skills"):
        versions.activate_version(db, a.payload["version_id"], a.id)
    elif a.kind == "content":
        d = db.get(HQDraft, a.payload.get("draft_id"))
        if d:
            d.status = "approved"
    a.status, a.decided_at, a.decided_by = "approved", utcnow(), actor
    if a.payload.get("proposal_id"):
        p = db.get(HQProposal, a.payload["proposal_id"])
        if p:
            p.status = "approved"
    db.commit()
    from .xp import award

    for agent in a.agents or []:
        award(db, agent, "proposal_approved", f"approval:{a.id}", note=a.title)
    bus.publish(db, "approval_decided", "manager", approval_id=a.id, status="approved", title=a.title)
    return a


def reject(db: Session, approval_id: int, actor: str, reason: str = "") -> HQApproval:
    a = db.get(HQApproval, approval_id)
    if a is None:
        raise ApprovalError("approval not found")
    if a.status != "pending":
        raise ApprovalError(f"already {a.status}")
    a.status, a.decided_at, a.decided_by = "rejected", utcnow(), actor
    a.payload = {**a.payload, "reject_reason": reason}
    if a.kind == "content":
        d = db.get(HQDraft, a.payload.get("draft_id"))
        if d:
            d.status = "rejected"
    if a.payload.get("proposal_id"):
        p = db.get(HQProposal, a.payload["proposal_id"])
        if p:
            p.status = "rejected"
    db.commit()
    bus.publish(db, "approval_decided", "manager", approval_id=a.id, status="rejected", title=a.title)
    return a
