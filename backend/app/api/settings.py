from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import audit, get_current_user, hash_password, new_link_code, require_admin, verify_password
from ..trading_config import TradingConfig, load_config, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])

PREF_KEYS = {"new_signal", "target_hit", "stop_loss", "expired", "daily_summary", "system_alerts"}


@router.get("")
def get_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = get_settings()
    return {
        "trading": load_config(db).model_dump(),
        "editable": user.role == "admin",
        "binance": {"base_url": s.binance_base_url, "mode": "public market data only (no API key, no trading, no withdrawals)"},
        "telegram": {"bot_configured": s.telegram_configured, "linked": bool(user.telegram_chat_id),
                     "webhook_secret_configured": bool(s.telegram_webhook_secret)},
        "news_provider_configured": bool(s.cryptopanic_api_key),
        "llm_narrative_configured": bool(s.anthropic_api_key),
        "notification_prefs": {k: (user.notification_prefs or {}).get(k, True) for k in sorted(PREF_KEYS)},
        "profile": {"email": user.email, "display_name": user.display_name, "role": user.role},
        "billing_enabled": s.billing_enabled,
    }


@router.put("/trading")
def put_trading(body: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        cfg = TradingConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))
    old = load_config(db).model_dump()
    save_config(db, cfg)
    audit(db, f"user:{admin.id}", "update_trading_config", before=old, after=cfg.model_dump())
    return cfg.model_dump()


class PrefsIn(BaseModel):
    prefs: dict[str, bool]


@router.put("/notifications")
def put_prefs(body: PrefsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bad = set(body.prefs) - PREF_KEYS
    if bad:
        raise HTTPException(422, f"unknown preference keys: {sorted(bad)}")
    user.notification_prefs = {**(user.notification_prefs or {}), **body.prefs}
    db.commit()
    return user.notification_prefs


class ProfileIn(BaseModel):
    display_name: str = Field(max_length=120)


@router.put("/profile")
def put_profile(body: ProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.display_name = body.display_name
    db.commit()
    return {"ok": True}


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


@router.put("/password")
def change_password(body: PasswordIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    audit(db, f"user:{user.id}", "password_changed")
    return {"ok": True}


@router.post("/telegram/link")
def telegram_link(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not get_settings().telegram_configured:
        raise HTTPException(503, "Telegram bot is not configured on the server (TELEGRAM_BOT_TOKEN)")
    user.telegram_link_code = new_link_code()
    db.commit()
    return {"code": user.telegram_link_code, "instructions": f"Send /start {user.telegram_link_code} to the bot"}


@router.delete("/telegram/link")
def telegram_unlink(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.telegram_chat_id, user.telegram_link_code = None, None
    db.commit()
    return {"ok": True}
