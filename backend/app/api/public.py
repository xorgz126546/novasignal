"""Unauthenticated endpoints for the landing page. Only real, computed data — never marketing numbers."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..agents import ALL_AGENTS
from ..agents.a11_chief_trading_officer import CONFIDENCE_WEIGHTS
from ..config import get_settings
from ..db import get_db
from ..models import PaperTrade
from ..trading_config import RiskConfig

router = APIRouter(prefix="/api", tags=["public"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    import os

    from ..models import ServiceState

    db.execute(text("SELECT 1"))
    tg = db.get(ServiceState, "telegram")
    return {"status": "ok", "app": get_settings().app_name,
            "version": os.environ.get("RENDER_GIT_COMMIT", "")[:7] or None,
            "telegram": "not_configured" if not get_settings().telegram_configured else (tg.status if tg else "pending"),
            # Telegram's own error text only (e.g. "401 Unauthorized"); the client never includes the token in errors.
            "telegram_error": (tg.last_error or "")[:160] if tg and tg.status != "ok" else None}


@router.get("/public/methodology")
def methodology(db: Session = Depends(get_db)):
    closed = db.scalar(select(func.count()).select_from(PaperTrade).where(PaperTrade.status == "CLOSED")) or 0
    r = RiskConfig()
    return {
        "agents": [{"number": a.number, "name": a.name, "responsibility": a.responsibility} for a in ALL_AGENTS],
        "confidence_weights": CONFIDENCE_WEIGHTS,
        "rules": {"market": "Binance Spot only (no futures, margin or leverage)", "max_signals_per_day": r.max_signals_per_day,
                  "target_move_pct": [r.target_move_min_pct, r.target_move_max_pct], "min_risk_reward": r.min_risk_reward,
                  "take_profit_targets": 1, "stop_losses": 1, "auto_execution": False},
        "track_record": {"closed_paper_trades": closed,
                         "published": False,
                         "note": "Performance is published only after the 30-day validation completes. "
                                 "No results are shown before then."},
        "pricing": {"usd_per_month": get_settings().subscription_price_usd_cents / 100,
                    "billing_active": get_settings().billing_enabled},
    }
