"""Database schema (SQLAlchemy 2.0). One consistent store: PostgreSQL in production, SQLite in dev/tests.

Three performance tracks are stored SEPARATELY and never merged:
  * theoretical  -> Signal.theoretical_* columns (raw levels, no costs)
  * paper        -> PaperTrade (simulated fills with fees, slippage, delay)
  * actual       -> UserTrade (what the user reported doing on Binance)
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TZDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo; PostgreSQL keeps it)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Role(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class SignalStatus(str, enum.Enum):
    ANALYZING = "ANALYZING"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    USER_ENTERED = "USER_ENTERED"
    USER_SKIPPED = "USER_SKIPPED"
    TARGET_HIT = "TARGET_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CLOSED_MANUALLY = "CLOSED_MANUALLY"


TERMINAL_STATUSES = {
    SignalStatus.TARGET_HIT,
    SignalStatus.STOP_LOSS_HIT,
    SignalStatus.EXPIRED,
    SignalStatus.CANCELLED,
    SignalStatus.CLOSED_MANUALLY,
}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(16), default=Role.USER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    telegram_link_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    notification_prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    paper_equity_usdt: Mapped[float] = mapped_column(Float, default=1000.0)

    subscription: Mapped["Subscription | None"] = relationship(back_populates="user", uselist=False)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    plan: Mapped[str] = mapped_column(String(32), default="pro_monthly")
    status: Mapped[str] = mapped_column(String(24), default="none")  # none|trial|active|past_due|cancelled|expired
    current_period_end: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="none")
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
    user: Mapped[User] = relationship(back_populates="subscription")


class BillingRecord(Base):
    __tablename__ = "billing_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(24))  # paid|failed|refunded
    period_start: Mapped[datetime] = mapped_column(TZDateTime())
    period_end: Mapped[datetime] = mapped_column(TZDateTime())
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ScanCycle(Base):
    __tablename__ = "scan_cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error|skipped
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Opportunity(Base):
    """Every evaluated candidate — approved OR rejected — for the validation program."""

    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("scan_cycles.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # approved|rejected
    rejection_reasons: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("scan_cycles.id"), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(48), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16))  # ok|error
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("scan_cycles.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    base_asset: Mapped[str] = mapped_column(String(16), default="")
    quote_asset: Mapped[str] = mapped_column(String(16), default="USDT")
    direction: Mapped[str] = mapped_column(String(8), default="BUY")  # spot only => BUY
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    expected_move_pct: Mapped[float] = mapped_column(Float)
    risk_reward: Mapped[float] = mapped_column(Float)
    net_expected_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    justification: Mapped[list] = mapped_column(JSON, default=list)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    position_sizing: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default=SignalStatus.ANALYZING.value, index=True)
    entry_valid_until: Mapped[datetime] = mapped_column(TZDateTime())
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    # --- theoretical track (raw levels, no fees) ---
    theoretical_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    theoretical_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    theoretical_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    theoretical_closed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    # --- CRYPTONEX HQ additions (nullable; classic signals leave them empty) ---
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # classic | hq
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invalidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # % of equity risked if SL is hit
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # Publisher score 0-100

    events: Mapped[list["SignalEvent"]] = relationship(back_populates="signal", order_by="SignalEvent.at")
    paper_trade: Mapped["PaperTrade | None"] = relationship(back_populates="signal", uselist=False)


class SignalEvent(Base):
    __tablename__ = "signal_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64), default="system")
    signal: Mapped[Signal] = relationship(back_populates="events")


class UserTrade(Base):
    """User-confirmed actions. A signal NEVER implies the user traded it."""

    __tablename__ = "user_trades"
    __table_args__ = (UniqueConstraint("user_id", "signal_id", name="uq_user_signal"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # ENTERED|SKIPPED
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN|CLOSED|SKIPPED
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)  # quote currency (USDT)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # actual entry vs signal entry
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN|CLOSED
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal: Mapped[Signal] = relationship(back_populates="paper_trade")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class ValidationProgram(Base):
    __tablename__ = "validation_programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(TZDateTime())
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|aborted
    notes: Mapped[str] = mapped_column(Text, default="")


class TelegramSession(Base):
    """Conversation state for multi-step Telegram flows (e.g. awaiting entry price)."""

    __tablename__ = "telegram_sessions"
    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    awaiting: Mapped[str | None] = mapped_column(String(32), nullable=True)  # entry_price|exit_price
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class ServiceState(Base):
    __tablename__ = "service_states"
    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok|degraded|error|stopped|unknown
    last_ok_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    restarts: Mapped[int] = mapped_column(Integer, default=0)
