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
