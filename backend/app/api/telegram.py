from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import audit, rate_limit, require_admin
from ..telegram.bot import get_client, handle_update, verify_secret
from ..telegram.client import TelegramError

log = logging.getLogger("cryptonex.telegram")
router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook", dependencies=[Depends(rate_limit(120, 60, "tg"))])
async def webhook(request: Request, db: Session = Depends(get_db),
                  x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not verify_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(401, "invalid webhook secret")
    client = get_client()
    if client is None:
        raise HTTPException(503, "bot not configured")
    if int(request.headers.get("content-length") or 0) > 100_000:
        raise HTTPException(413, "payload too large")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(update, dict):
        raise HTTPException(400, "invalid update")
    try:
        await handle_update(db, update, client)
    except TelegramError as e:
        log.warning("telegram reply failed: %s", e)
    except Exception:
        log.exception("telegram update handling failed")
    return {"ok": True}  # always 200 so Telegram doesn't retry-storm


@router.post("/setup-webhook")
async def setup_webhook(public_api_url: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..telegram.bot import register_webhook

    if not public_api_url.startswith("https://"):
        raise HTTPException(400, "Telegram requires an https URL")
    try:
        me = await register_webhook(public_api_url)
    except TelegramError as e:
        raise HTTPException(502, str(e))
    audit(db, f"user:{admin.id}", "telegram_webhook_set", url=public_api_url)
    return {"ok": True, "bot": me.get("username")}


@router.post("/test")
async def test_message(admin: User = Depends(require_admin)):
    client = get_client()
    if client is None:
        raise HTTPException(503, "bot not configured")
    if not admin.telegram_chat_id:
        raise HTTPException(400, "link your Telegram first")
    try:
        await client.send_message(admin.telegram_chat_id, "✅ CRYPTONEX AI test message — Telegram is connected.")
    except TelegramError as e:
        raise HTTPException(502, str(e))
    return {"ok": True}
