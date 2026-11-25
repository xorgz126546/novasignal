"""Owner notifications (Telegram chat TELEGRAM_CHAT_ID). Failures are logged, never raised."""
from __future__ import annotations

import logging

from ..config import get_settings

log = logging.getLogger("cryptonex.hq.notify")
MAX_LEN = 3900  # Telegram message limit is 4096


def owner_chats() -> list[str]:
    """Where the owner is reachable: every linked, active admin's private chat + TELEGRAM_CHAT_ID (chat or channel)."""
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import User

    s = get_settings()
    with SessionLocal() as db:
        ids = [u.telegram_chat_id for u in db.scalars(select(User).where(
            User.role == "admin", User.is_active.is_(True), User.telegram_chat_id.is_not(None)).order_by(User.id))]
    if s.telegram_chat_id and s.telegram_chat_id not in ids:
        ids.append(s.telegram_chat_id)
    return ids


async def notify_owner(text: str, reply_markup: dict | None = None) -> bool:
    """Contact the owner directly. True if at least one owner chat received the message."""
    s = get_settings()
    if not s.telegram_bot_token:
        return False
    from ..telegram.bot import get_client
    from ..telegram.client import TelegramError

    client = get_client()
    if client is None:
        return False
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
    delivered = False
    for chat in owner_chats():
        try:
            for i, c in enumerate(chunks):
                await client.send_message(chat, c, reply_markup if i == len(chunks) - 1 else None)
            delivered = True
        except TelegramError as e:
            log.warning("owner notification to %s failed: %s", "channel" if str(chat).startswith("-") else "admin chat", e)
    return delivered


def approval_keyboard(approval_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "✅ Approve / موافق", "callback_data": f"ap:{approval_id}"},
                                 {"text": "❌ Reject / رفض", "callback_data": f"rj:{approval_id}"}]]}


def approval_card(a) -> str:
    from html import escape

    gate = (a.payload or {}).get("gate") or {}
    s = gate.get("summary") or {}
    lines = [f"🔔 <b>Needs you #{a.id}</b> — {escape(a.kind)}", f"<b>{escape(a.title[:300])}</b>", "", escape((a.summary or "")[:1500])]
    if s.get("trades") is not None:
        lines += ["", f"Backtest on real history: {s.get('trades')} trades · win rate "
                      f"{(s.get('win_rate') or 0) * 100:.0f}% · net {s.get('expectancy_pct')}%/trade after fees"]
    if (a.payload or {}).get("tools"):
        lines.append("🔧 New analysis tool(s): " + ", ".join(escape(t) for t in a.payload["tools"]))
    if (a.payload or {}).get("source_url"):
        lines.append(f"Source: {escape(a.payload['source_url'])}")
    lines += ["", "<i>Approve = activated as a new version (one-click rollback in the dashboard). Nothing is live before "
                  "you approve. Not financial advice.</i>"]
    return "\n".join(lines)


async def send_approval(approval_id: int) -> bool:
    """Push a 'Needs you' item to the owner's Telegram with Approve / Reject buttons."""
    from ..db import SessionLocal
    from .models import HQApproval

    with SessionLocal() as db:
        a = db.get(HQApproval, approval_id)
        if a is None or a.status != "pending":
            return False
        text = approval_card(a)
    return await notify_owner(text, approval_keyboard(approval_id))


async def welcome_owner_once() -> None:
    """Send ONE confirmation message to TELEGRAM_CHAT_ID (per chat id) and record the delivery status,
    so a wrong id / missing admin rights shows up immediately on /api/health."""
    from ..db import SessionLocal
    from ..engine.services import set_service_state
    from ..models import AppSetting

    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return
    with SessionLocal() as db:
        key = f"owner_chat_welcomed:{s.telegram_chat_id}"
        if db.get(AppSetting, key):
            return
        from ..telegram.bot import get_client
        from ..telegram.client import TelegramError

        client = get_client()
        if client is None:
            return
        try:
            await client.send_message(s.telegram_chat_id, "✅ <b>CRYPTONEX HQ</b> is connected to this chat.\n"
                                      "You will receive here: the daily best pick (or NO TRADE + triggers), outcomes, "
                                      "briefs and alerts.\n<i>Not financial advice.</i>")
            db.add(AppSetting(key=key, value={"ok": True}))
            db.commit()
            set_service_state(db, "owner_chat", ok=True)
        except TelegramError as e:
            set_service_state(db, "owner_chat", ok=False, error=str(e)[:500])
