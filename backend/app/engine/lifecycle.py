"""Signal lifecycle: status transitions, user reports, paper trades and outcome evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market.binance import Candle
from ..models import (TERMINAL_STATUSES, PaperTrade, Role, Signal, SignalEvent, SignalStatus, User, UserTrade,
                      utcnow)
from ..trading_config import RiskConfig

S = SignalStatus

ALLOWED = {
    S.ANALYZING: {S.APPROVED, S.CANCELLED},
    S.APPROVED: {S.ACTIVE, S.CANCELLED},
    S.ACTIVE: {S.USER_ENTERED, S.USER_SKIPPED, S.TARGET_HIT, S.STOP_LOSS_HIT, S.EXPIRED, S.CANCELLED},
    S.USER_ENTERED: {S.TARGET_HIT, S.STOP_LOSS_HIT, S.EXPIRED, S.CLOSED_MANUALLY, S.CANCELLED},
    S.USER_SKIPPED: {S.TARGET_HIT, S.STOP_LOSS_HIT, S.EXPIRED, S.CANCELLED},
}


class LifecycleError(ValueError):
    pass


def transition(db: Session, sig: Signal, to: SignalStatus, note: str = "", actor: str = "system") -> None:
    cur = SignalStatus(sig.status)
    if to not in ALLOWED.get(cur, set()):
        raise LifecycleError(f"illegal transition {cur.value} -> {to.value}")
    db.add(SignalEvent(signal_id=sig.id, from_status=cur.value, to_status=to.value, note=note, actor=actor))
    sig.status = to.value
    if to in TERMINAL_STATUSES:
        sig.closed_at = utcnow()


# ---------------- user reports (never assume a user traded) ----------------

def open_user_trades(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(UserTrade).where(UserTrade.user_id == user_id,
                                                                       UserTrade.status == "OPEN")) or 0


def _apply_global_status(db: Session, user: User, sig: Signal, to: SignalStatus, note: str) -> None:
    """Global USER_* statuses reflect the OPERATOR's (admin's) own actions during the validation phase.
    Every user's own decision is always stored on their UserTrade row."""
    if user.role == Role.ADMIN.value and to in ALLOWED.get(SignalStatus(sig.status), set()):
        transition(db, sig, to, note, actor=f"user:{user.id}")


def report_entered(db: Session, user: User, sig: Signal, price: float, quantity: float | None = None,
                   fees: float = 0.0, notes: str = "", risk: RiskConfig | None = None) -> UserTrade:
    if price <= 0:
        raise LifecycleError("entry price must be positive")
    if quantity is not None and quantity <= 0:
        raise LifecycleError("quantity must be positive")
    existing = db.scalar(select(UserTrade).where(UserTrade.user_id == user.id, UserTrade.signal_id == sig.id))
    if existing:
        raise LifecycleError(f"already reported {existing.decision} for this signal")
    if SignalStatus(sig.status) in TERMINAL_STATUSES:
        raise LifecycleError(f"signal is {sig.status}; cannot enter")
    risk = risk or RiskConfig()
    if open_user_trades(db, user.id) >= risk.max_open_user_trades:
        raise LifecycleError(f"max {risk.max_open_user_trades} open user-confirmed trade(s); close the current one first")
    if abs(price - sig.entry_price) / sig.entry_price > 0.2:
        raise LifecycleError("entry price deviates >20% from signal entry — please check the value")
    t = UserTrade(user_id=user.id, signal_id=sig.id, decision="ENTERED", status="OPEN", entry_price=price,
                  quantity=quantity, entry_at=utcnow(), fees=fees or 0.0, notes=notes,
                  slippage_pct=(price - sig.entry_price) / sig.entry_price * 100)
    db.add(t)
    _apply_global_status(db, user, sig, S.USER_ENTERED, f"entered at {price}")
    db.commit()
    return t


def report_skipped(db: Session, user: User, sig: Signal, notes: str = "") -> UserTrade:
    existing = db.scalar(select(UserTrade).where(UserTrade.user_id == user.id, UserTrade.signal_id == sig.id))
    if existing:
        raise LifecycleError(f"already reported {existing.decision} for this signal")
    t = UserTrade(user_id=user.id, signal_id=sig.id, decision="SKIPPED", status="SKIPPED", notes=notes)
    db.add(t)
    _apply_global_status(db, user, sig, S.USER_SKIPPED, "user skipped")
    db.commit()
    return t


def report_closed(db: Session, user: User, sig: Signal, exit_price: float, fees: float = 0.0,
                  quantity: float | None = None, notes: str = "") -> UserTrade:
    t = db.scalar(select(UserTrade).where(UserTrade.user_id == user.id, UserTrade.signal_id == sig.id))
    if not t or t.decision != "ENTERED":
        raise LifecycleError("no ENTERED trade to close for this signal")
    if t.status == "CLOSED":
        raise LifecycleError("trade already closed")
    if exit_price <= 0 or fees < 0:
        raise LifecycleError("exit price must be positive and fees non-negative")
    if quantity is not None:
        if quantity <= 0:
            raise LifecycleError("quantity must be positive")
        t.quantity = quantity
    t.exit_price, t.exit_at = exit_price, utcnow()
    t.fees = (t.fees or 0.0) + fees
    gross_pct = (exit_price - t.entry_price) / t.entry_price * 100
    if t.quantity:
        notional = t.quantity * t.entry_price
        t.realized_pnl = t.quantity * (exit_price - t.entry_price) - t.fees
        t.realized_pct = t.realized_pnl / notional * 100
    else:
        t.realized_pnl = None
        t.realized_pct = gross_pct  # fees can't be converted to % without quantity
        notes = (notes + " " if notes else "") + "[quantity unknown: % excludes fees]"
    t.status = "CLOSED"
    if notes:
        t.notes = (t.notes + "\n" if t.notes else "") + notes
    if user.role == Role.ADMIN.value and SignalStatus(sig.status) == S.USER_ENTERED:
        transition(db, sig, S.CLOSED_MANUALLY, f"user closed at {exit_price}", actor=f"user:{user.id}")
    db.commit()
    return t


# ---------------- paper trading ----------------

def open_paper_trade(db: Session, sig: Signal, fill_reference_price: float, risk: RiskConfig) -> PaperTrade:
    """Simulated fill: price observed AFTER approval (execution delay) plus adverse slippage."""
    entry = fill_reference_price * (1 + risk.slippage_pct / 100)
    qty = float(sig.position_sizing.get("quantity") or 0) or (1000 * risk.max_position_pct / 100 / entry)
    p = PaperTrade(signal_id=sig.id, status="OPEN", entry_price=entry, quantity=qty, opened_at=utcnow(), fees=0.0,
                   slippage_cost=qty * (entry - fill_reference_price))
    sig.paper_trade = p  # attach so outcomes in the same session close it
    db.add(p)
    return p


def close_paper_trade(p: PaperTrade, outcome: str, level_price: float, risk: RiskConfig, at: datetime) -> None:
    slip = risk.slippage_pct / 100
    if outcome == "TARGET_HIT":
        exit_price = level_price  # resting limit order: no slippage
        extra_slip = 0.0
    else:  # stop-market / market exit: adverse slippage (doubled for stops)
        mult = 2 if outcome == "STOP_LOSS_HIT" else 1
        exit_price = level_price * (1 - mult * slip)
        extra_slip = p.quantity * (level_price - exit_price)
    fee = risk.fee_rate_pct / 100
    fees = fee * p.quantity * (p.entry_price + exit_price)
    p.exit_price, p.closed_at, p.outcome, p.status = exit_price, at, outcome, "CLOSED"
    p.fees = fees
    p.slippage_cost += extra_slip
    p.pnl = p.quantity * (exit_price - p.entry_price) - fees
    p.return_pct = p.pnl / (p.quantity * p.entry_price) * 100


# ---------------- outcome evaluation (theoretical track) ----------------

@dataclass
class Outcome:
    outcome: str  # TARGET_HIT | STOP_LOSS_HIT | EXPIRED
    price: float
    at: datetime


def evaluate_candles(sig: Signal, candles: list[Candle], now: datetime) -> Outcome | None:
    """Walk closed 1m candles. Conservative: if a candle touches both SL and TP, SL is assumed first."""
    created_ms = int(sig.created_at.timestamp() * 1000)
    expires_ms = int(sig.expires_at.timestamp() * 1000)
    last_close = None
    reached_expiry = False
    for c in candles:
        if c.close_time <= created_ms:
            continue
        if c.open_time >= expires_ms:
            reached_expiry = True
            break
        at = datetime.fromtimestamp(c.close_time / 1000, timezone.utc)
        if c.low <= sig.stop_loss:
            return Outcome("STOP_LOSS_HIT", sig.stop_loss, at)
        if c.high >= sig.take_profit:
            return Outcome("TARGET_HIT", sig.take_profit, at)
        last_close = c.close
        if c.close_time >= expires_ms - 1:
            reached_expiry = True
            break
    # Only expire once data actually covers the expiry time (a partial backlog must not expire early).
    if now >= sig.expires_at and reached_expiry:
        price = last_close if last_close is not None else sig.last_price
        if price is not None:
            return Outcome("EXPIRED", price, sig.expires_at)
    return None


def apply_outcome(db: Session, sig: Signal, o: Outcome, risk: RiskConfig) -> None:
    sig.theoretical_outcome = o.outcome
    sig.theoretical_exit_price = o.price
    sig.theoretical_return_pct = (o.price - sig.entry_price) / sig.entry_price * 100
    sig.theoretical_closed_at = o.at
    if sig.paper_trade and sig.paper_trade.status == "OPEN":
        close_paper_trade(sig.paper_trade, o.outcome, o.price, risk, o.at)
    cur = SignalStatus(sig.status)
    target = SignalStatus(o.outcome)
    if target in ALLOWED.get(cur, set()):
        transition(db, sig, target, f"{o.outcome} at {o.price:.8g}")
