"""Owner chat/channel (TELEGRAM_CHAT_ID) receives picks, NO TRADE notes and alerts; failures are isolated."""
import json

import httpx

from app.config import get_settings
from app.models import Signal, User, utcnow
from app.telegram import bot
from app.telegram.client import TelegramClient


def _client(log, fail_chat=None):
    def h(req):
        body = json.loads(req.content or b"{}")
        log.append(body)
        if body.get("chat_id") == fail_chat:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200, json={"ok": True, "result": {}})
    return TelegramClient("123:TEST", transport=httpx.MockTransport(h))


def _signal(db):
    now = utcnow()
    s = Signal(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", entry_price=100, take_profit=104, stop_loss=98.7,
               expected_move_pct=4, net_expected_return_pct=3.7, risk_reward=3, confidence=70, status="ACTIVE",
               created_at=now, entry_valid_until=now, expires_at=now)
    db.add(s)
    db.commit()
    return s


async def test_signal_reaches_owner_channel_and_linked_users(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "-1001716039170")
    db.add(User(email="a@example.com", password_hash="x", role="admin", telegram_chat_id="555"))
    db.commit()
    log = []
    bot.set_client(_client(log))
    try:
        await bot.notify("new_signal", _signal(db))
        await bot.notify("text", "NO TRADE today")
    finally:
        bot.set_client(None)
    chats = [m["chat_id"] for m in log]
    assert chats.count("555") == 2 and chats.count("-1001716039170") == 2
    chan = [m for m in log if m["chat_id"] == "-1001716039170"]
    assert "reply_markup" not in chan[0]  # no ENTERED/SKIPPED buttons in a channel
    assert "reply_markup" in [m for m in log if m["chat_id"] == "555"][0]


async def test_one_failing_recipient_does_not_block_others(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "-100bad")
    db.add(User(email="a@example.com", password_hash="x", role="admin", telegram_chat_id="555"))
    db.commit()
    log = []
    bot.set_client(_client(log, fail_chat="-100bad"))
    try:
        await bot.notify("text", "hello")
    finally:
        bot.set_client(None)
    assert {m["chat_id"] for m in log} == {"555", "-100bad"}
    from app.models import ServiceState
    assert "chat not found" in db.get(ServiceState, "telegram").last_error


async def test_owner_welcome_is_sent_once_and_reported(db, monkeypatch):
    from app.hq.notify import welcome_owner_once
    from app.models import ServiceState
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "-1001716039170")
    log = []
    bot.set_client(_client(log))
    try:
        await welcome_owner_once()
        await welcome_owner_once()
    finally:
        bot.set_client(None)
    assert len(log) == 1 and "connected" in log[0]["text"]
    assert db.get(ServiceState, "owner_chat").status == "ok"


def test_health_reports_owner_chat_and_daily_pick(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "-1001716039170")
    with TestClient(app) as c:
        h = c.get("/api/health").json()
    assert h["owner_chat"] in ("pending", "ok") or h["owner_chat"].startswith("error")
    assert set(h["daily_pick"]) >= {"enabled", "last_run", "last_result"}
