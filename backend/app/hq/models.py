"""CRYPTONEX HQ tables. Everything the agent company does is persisted here."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import TZDateTime, utcnow


class HQAgent(Base):
    """Live state of each agent (static definition lives in hq/config/agents.yaml)."""

    __tablename__ = "hq_agents"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|working|blocked
    current_task: Mapped[str] = mapped_column(Text, default="")
    last_active_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class HQTask(Base):
    __tablename__ = "hq_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(48), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|failed|blocked
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HQEvent(Base):
    __tablename__ = "hq_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class HQChatMessage(Base):
    __tablename__ = "hq_chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    role: Mapped[str] = mapped_column(String(16))  # owner|manager
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class HQReport(Base):
    __tablename__ = "hq_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (local timezone)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # agent_daily|brief|weekly|alert|incident
    agent_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_telegram: Mapped[bool] = mapped_column(Boolean, default=False)


class HQChecklistItem(Base):
    __tablename__ = "hq_checklist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), default="manager")
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)  # dedup key


class HQApproval(Base):
    """'Needs you' queue: nothing listed here changes live behaviour until the owner approves."""

    __tablename__ = "hq_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # config|rule_strategy|prompt|skills|fix_pr|content
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agents: Mapped[list] = mapped_column(JSON, default=list)  # agents credited on approval
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)


class HQConfigVersion(Base):
    """Versioned live configuration. Exactly one active version per key; rollback re-activates an older one."""

    __tablename__ = "hq_config_versions"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_hq_cfg_key_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(96), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class HQProposal(Base):
    __tablename__ = "hq_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    agent_id: Mapped[str] = mapped_column(String(48))
    kind: Mapped[str] = mapped_column(String(32))  # rule_strategy|parameter|prompt|skills|code
    title: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    knowledge_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")  # queued|testing|passed|failed|approved|rejected
    gate: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HQKnowledge(Base):
    __tablename__ = "hq_knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    slug: Mapped[str] = mapped_column(String(128), unique=True)
    source_url: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(64))
    license_class: Mapped[str] = mapped_column(String(16))  # adaptable|ideas_only
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(Text)
    idea: Mapped[str] = mapped_column(Text, default="")
    how_to_test: Mapped[str] = mapped_column(Text, default="")
    injection_flags: Mapped[list] = mapped_column(JSON, default=list)
    body_md: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="noted")  # noted|distilled|proposed|rejected


class HQXPEvent(Base):
    """XP only from verified events. (source_type, source_ref, agent_id) is unique → never double counted."""

    __tablename__ = "hq_xp_events"
    __table_args__ = (UniqueConstraint("agent_id", "source_type", "source_ref", name="uq_hq_xp_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    agent_id: Mapped[str] = mapped_column(String(48), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(128))
    xp: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")


class HQLLMUsage(Base):
    __tablename__ = "hq_llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    agent_id: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    purpose: Mapped[str] = mapped_column(String(64), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)


class HQBacktestRun(Base):
    __tablename__ = "hq_backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    subject: Mapped[str] = mapped_column(String(128))  # strategy id / proposal ref
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class HQIncident(Base):
    __tablename__ = "hq_incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(48))  # stale_data|feed_down|repeated_errors|job_failed
    key: Mapped[str] = mapped_column(String(128), index=True)  # dedup key while open
    title: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|fix_proposed|resolved
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class HQDraft(Base):
    __tablename__ = "hq_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    kind: Mapped[str] = mapped_column(String(24))  # post|reel|signal_card
    lang: Mapped[str] = mapped_column(String(8))  # ar|en|both
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved|rejected
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HQDecision(Base):
    """Each trading-cycle outcome per candidate: a signal or NO TRADE with the reason."""

    __tablename__ = "hq_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    cycle_ref: Mapped[str] = mapped_column(String(48), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(16))  # SIGNAL|NO_TRADE
    stage: Mapped[str] = mapped_column(String(24))  # scanner|structure|strategy|risk|publisher
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    levels: Mapped[dict] = mapped_column(JSON, default=dict)  # hypothetical levels (for "avoided loss" audit)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    audited: Mapped[bool] = mapped_column(Boolean, default=False)
    audit_result: Mapped[str | None] = mapped_column(String(24), nullable=True)


class HQCandleCache(Base):
    __tablename__ = "hq_candle_cache"
    __table_args__ = (UniqueConstraint("symbol", "interval", name="uq_hq_candles"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    candles: Mapped[list] = mapped_column(JSON, default=list)
