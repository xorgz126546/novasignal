"""Owner notifications (Telegram chat TELEGRAM_CHAT_ID). Failures are logged, never raised."""
from __future__ import annotations

import logging

from ..config import get_settings

log = logging.getLogger("cryptonex.hq.notify")
MAX_LEN = 3900  # Telegram message limit is 4096


async def notify_owner(text: str) -> bool:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return False
    from ..telegram.bot import get_client
    from ..telegram.client import TelegramError

    client = get_client()
    if client is None:
        return False
    try:
        chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
        for c in chunks:
            await client.send_message(s.telegram_chat_id, c)
        return True
    except TelegramError as e:
        log.warning("owner notification failed: %s", e)
        return False


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
