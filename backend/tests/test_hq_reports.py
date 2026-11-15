"""Reports, morning brief, checklist, scheduler due-logic, owner Telegram."""
import json
from datetime import datetime, timedelta, timezone

import httpx

from app.hq import approvals, reports, versions
from app.hq.models import HQChecklistItem, HQIncident, HQReport
from app.hq.scheduler import is_due
from app.models import Signal, utcnow
from app.telegram import bot
from app.telegram.client import TelegramClient

ALG = timezone(timedelta(hours=1))  # Africa/Algiers is UTC+1 all year (no DST)


def at(h, m=0, day=13):
    return datetime(2026, 11, day, h, m, tzinfo=ALG).astimezone(timezone.utc)


def test_is_due_daily_weekly_every():
    assert not is_due("daily 08:00", None, at(7, 59))
    assert is_due("daily 08:00", None, at(8, 0))
    assert not is_due("daily 08:00", at(8, 1), at(9, 0))  # already ran today
    assert is_due("daily 08:00", at(8, 1, day=12), at(8, 5))  # ran yesterday
    assert is_due("twice_daily 09:00,18:00", at(9, 5), at(18, 1))
    # 2026-11-13 is a Friday
    assert is_due("weekly FRI 17:00", None, at(17, 30))
    assert not is_due("weekly MON 07:00", None, at(8, 0))
    assert is_due("every 10m", None, at(1)) and not is_due("every 10m", at(1), at(1, 5)) and is_due("every 10m", at(1), at(1, 11))


def test_checklist_from_real_state_and_auto_complete(db):
    v = versions.create_candidate(db, "publisher", {"min_score": 62}, "publisher")
    db.commit()
    a = approvals.request(db, "config", "Raise min score to 62", "", {"version_id": v.id}, ["publisher"])
    now = utcnow()
    s = Signal(symbol="BTCUSDT", base_asset="BTC", entry_price=1, take_profit=2, stop_loss=0.5, expected_move_pct=4,
               risk_reward=2, confidence=70, status="ACTIVE", created_at=now, entry_valid_until=now, expires_at=now)
    db.add(s)
    db.commit()
    reports.refresh_checklist(db)
    reports.refresh_checklist(db)  # no duplicates
    texts = [c.text for c in db.query(HQChecklistItem).filter_by(done=False)]
    assert any("Raise min score" in t for t in texts) and any(f"#{s.id}" in t for t in texts)
    assert any("ANTHROPIC_API_KEY" in t for t in texts)
    assert len(texts) == len(set(texts))
    approvals.approve(db, a.id, "user:1")
    reports.refresh_checklist(db)
    assert db.query(HQChecklistItem).filter_by(ref=f"approval:{a.id}").one().done


async def test_brief_has_all_sections_and_only_db_numbers(db):
    db.add(HQIncident(kind="feed_down", key="feed:binance", title="Binance feed failing", details={}))
    db.commit()
    rep = await reports.build_brief(db, send=False)
    md = rep.body_md
    for section in ("Your part today", "Needs you", "What broke", "Level-ups", "Yesterday"):
        assert section in md
    assert "Binance feed failing" in md and "ليست نصيحة مالية" in md
    assert db.query(HQReport).filter_by(kind="brief").count() == 1


async def test_agent_daily_reports(db):
    out = await reports.write_all_agent_reports(db)
    assert len(out) == 14  # every agent except the manager reports to the manager
    r = next(x for x in out if x.agent_id == "risk")
    assert "Numbers (from the database)" in r.body_md and "vetoes" in r.body_md


def test_md_to_telegram_escapes_html():
    out = reports.md_to_telegram("# Title\n- <script>x</script> **bold**")
    assert "<b>Title</b>" in out and "&lt;script&gt;" in out and "<b>bold</b>" in out


async def test_brief_sent_to_owner_chat(db, monkeypatch):
    from app.config import get_settings
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})
    bot.set_client(TelegramClient("123:TEST", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "999")
    try:
        rep = await reports.build_brief(db, send=True)
        assert rep.sent_telegram and sent and sent[0]["chat_id"] == "999" and "Morning brief" in sent[0]["text"]
    finally:
        bot.set_client(None)


def test_owner_can_chat_with_manager_from_telegram(db):
    from fastapi.testclient import TestClient

    from app.main import app
    sent = []

    def handler(req):
        if req.url.path.endswith("sendMessage"):
            sent.append(json.loads(req.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"username": "bot"}})
    bot.set_client(TelegramClient("123:TEST", transport=httpx.MockTransport(handler)))
    try:
        with TestClient(app) as c:
            r = c.post("/api/auth/register", json={"email": "o@example.com", "password": "CorrectHorse9battery",
                                                    "accept_risk_disclosure": True})
            h = {"Authorization": f"Bearer {r.json()['token']}"}
            code = c.post("/api/settings/telegram/link", headers=h).json()["code"]
            hdr = {"X-Telegram-Bot-Api-Secret-Token": "whsecret"}
            c.post("/api/telegram/webhook", headers=hdr, json={"update_id": 1, "message": {"chat": {"id": 5}, "text": f"/start {code}"}})
            c.post("/api/telegram/webhook", headers=hdr, json={"update_id": 2, "message": {"chat": {"id": 5}, "text": "ما حالة الفريق؟"}})
        assert "الفريق" in sent[-1]
    finally:
        bot.set_client(None)
