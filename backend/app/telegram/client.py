"""Minimal Telegram Bot API client (httpx). The token never leaves the backend."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("cryptonex.telegram")


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org",
                 transport: httpx.AsyncBaseTransport | None = None):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN not configured")
        self._base = f"{api_base.rstrip('/')}/bot{token}"
        self._client = httpx.AsyncClient(timeout=10, transport=transport)

    async def call(self, method: str, **payload) -> dict:
        try:
            r = await self._client.post(f"{self._base}/{method}", json={k: v for k, v in payload.items() if v is not None})
        except httpx.HTTPError as e:
            raise TelegramError(f"network error calling {method}: {type(e).__name__}") from None  # don't leak URL/token
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not data.get("ok"):
            raise TelegramError(f"{method} failed: {r.status_code} {data.get('description', '')}")
        return data["result"]

    async def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> dict:
        return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                               disable_web_page_preview=True, reply_markup=reply_markup)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    async def set_webhook(self, url: str, secret: str) -> dict:
        return await self.call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"],
                               drop_pending_updates=True)

    async def delete_webhook(self) -> dict:
        return await self.call("deleteWebhook")

    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        return await self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"])

    async def close(self) -> None:
        await self._client.aclose()
