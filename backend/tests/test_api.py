"""HTTP API, auth/RBAC, rate limiting, Telegram webhook (mocked transport), reports, exports."""
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Signal, SignalStatus, User, utcnow
from app.telegram import bot
from app.telegram.client import TelegramClient

PW = "CorrectHorse9battery"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def register(c, email, pw=PW):
    r = c.post("/api/auth/register", json={"email": email, "password": pw, "accept_risk_disclosure": True})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]


def seed_signal(db, **kw):
    now = utcnow()
    s = Signal(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", entry_price=90000, take_profit=93600,
               stop_loss=89100, expected_move_pct=4, net_expected_return_pct=3.7, risk_reward=4, confidence=72,
               status=SignalStatus.ACTIVE.value, created_at=now, entry_valid_until=now + timedelta(hours=4),
               expires_at=now + timedelta(hours=48), justification=["[trend] up"], agent_outputs={}, **kw)
    db.add(s)
    db.commit()
    return s


def test_health_and_security_headers(client):
    r = client.get("/api/health")
    assert r.json()["status"] == "ok"
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_public_methodology_has_no_fabricated_results(client):
    m = client.get("/api/public/methodology").json()
    assert len(m["agents"]) == 11
    assert m["track_record"]["published"] is False
    assert m["rules"]["auto_execution"] is False


def test_register_login_roles(client):
    h_admin, u_admin = register(client, "owner@test.io")
    h_user, u_user = register(client, "user@test.io")
    assert u_admin["role"] == "admin" and u_user["role"] == "user"
    assert client.post("/api/auth/register", json={"email": "owner@test.io", "password": PW,
                                                   "accept_risk_disclosure": True}).status_code == 409
    assert client.post("/api/auth/register", json={"email": "x@test.io", "password": PW,
                                                   "accept_risk_disclosure": False}).status_code == 400
    assert client.post("/api/auth/register", json={"email": "y@test.io", "password": "short",
                                                   "accept_risk_disclosure": True}).status_code == 422
    assert client.post("/api/auth/login", json={"email": "user@test.io", "password": PW}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "user@test.io", "password": "wrongpass1"}).status_code == 401
    assert client.get("/api/auth/me", headers=h_user).json()["email"] == "user@test.io"
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    # RBAC
    assert client.get("/api/admin/stats", headers=h_user).status_code == 403
    assert client.get("/api/admin/stats", headers=h_admin).status_code == 200
    assert client.put("/api/settings/trading", headers=h_user, json={}).status_code == 403


def test_login_rate_limited(client):
    register(client, "rl@test.io")
    codes = [client.post("/api/auth/login", json={"email": "rl@test.io", "password": "wrongpass1"}).status_code
             for _ in range(12)]
    assert 429 in codes


def test_signal_flow_via_api(client, db):
    h_admin, _ = register(client, "owner@test.io")
    h_user, _ = register(client, "user@test.io")
    s = seed_signal(db)
    lst = client.get("/api/signals", headers=h_user).json()
    assert lst[0]["id"] == s.id and lst[0]["my_decision"] is None
    d = client.get(f"/api/signals/{s.id}", headers=h_user).json()
    assert d["take_profit"] == 93600 and d["stop_loss"] == 89100
    assert client.post(f"/api/signals/{s.id}/report", headers=h_user, json={"action": "ENTERED"}).status_code == 400
    r = client.post(f"/api/signals/{s.id}/report", headers=h_user, json={"action": "ENTERED", "price": 90050, "quantity": 0.01})
    assert r.status_code == 200
    r = client.post(f"/api/signals/{s.id}/report", headers=h_user,
                    json={"action": "CLOSED", "price": 93500, "fees": 1.8, "notes": "tp"})
    assert r.status_code == 200
    j = client.get("/api/trades", headers=h_user).json()[0]
    assert j["status"] == "CLOSED" and j["realized_pnl"] == pytest.approx(0.01 * (93500 - 90050) - 1.8)
    assert client.patch(f"/api/trades/{j['id']}", headers=h_user, json={"notes": "good"}).status_code == 200
    assert client.patch(f"/api/trades/{j['id']}", headers=h_admin, json={"notes": "x"}).status_code == 404  # not owner
    a = client.get("/api/analytics?track=actual", headers=h_user).json()
    assert a["metrics"]["trades"] == 1
    assert client.get("/api/analytics?track=mixed", headers=h_user).status_code == 422  # tracks can't be combined
    o = client.get("/api/overview", headers=h_user).json()
    assert set(o["tracks"]) == {"theoretical", "paper", "actual"} and len(o["agents"]) == 11


def test_settings_validation_and_pause(client):
    h_admin, _ = register(client, "owner@test.io")
    cfg = client.get("/api/settings", headers=h_admin).json()["trading"]
    cfg["risk"]["min_risk_reward"] = 2.5
    assert client.put("/api/settings/trading", headers=h_admin, json=cfg).status_code == 200
    cfg["risk"]["target_move_min_pct"] = 9  # > max
    assert client.put("/api/settings/trading", headers=h_admin, json=cfg).status_code == 422
    assert client.post("/api/admin/pause", headers=h_admin).json()["paused"] is True
    assert client.get("/api/overview", headers=h_admin).json()["paused"] is True
    audit = client.get("/api/admin/audit", headers=h_admin).json()
    assert any(a["action"] == "pause_signals" for a in audit)
    assert client.put("/api/settings/notifications", headers=h_admin, json={"prefs": {"bogus": True}}).status_code == 422


def test_validation_program_and_reports(client, db):
    h_admin, _ = register(client, "owner@test.io")
    p = client.post("/api/validation", headers=h_admin, json={"days": 30}).json()
    assert client.post("/api/validation", headers=h_admin, json={"days": 30}).status_code == 409
    seed_signal(db)
    for kind in ("daily", "weekly", "final"):
        rep = client.get(f"/api/validation/{p['id']}/report?kind={kind}", headers=h_admin).json()
        assert rep["signals"]["generated"] == 1 and set(rep["tracks"]) == {"theoretical", "paper", "actual"}
    md = client.get(f"/api/validation/{p['id']}/report?kind=final&format=md", headers=h_admin)
    assert "INCONCLUSIVE" in md.text


def test_admin_exports_and_users(client, db):
    h_admin, _ = register(client, "owner@test.io")
    _, u = register(client, "user@test.io")
    seed_signal(db)
    csv = client.get("/api/admin/export/signals.csv", headers=h_admin)
    assert csv.status_code == 200 and "BTCUSDT" in csv.text
    assert client.patch(f"/api/admin/users/{u['id']}", headers=h_admin, json={"is_active": False}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "user@test.io", "password": PW}).status_code == 403
    me_id = client.get("/api/auth/me", headers=h_admin).json()["id"]
    assert client.patch(f"/api/admin/users/{me_id}", headers=h_admin, json={"role": "user"}).status_code == 400


def test_billing_gate(client, monkeypatch):
    from app.config import get_settings
    h_admin, _ = register(client, "owner@test.io")
    h_user, u = register(client, "user@test.io")
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    assert client.get("/api/signals", headers=h_user).status_code == 402
    assert client.get("/api/signals", headers=h_admin).status_code == 200
    client.patch(f"/api/admin/users/{u['id']}", headers=h_admin, json={"subscription_status": "active"})
    assert client.get("/api/signals", headers=h_user).status_code == 200


# ---------------- Telegram (Bot API mocked; never contacts Telegram) ----------------
class TGRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content or b"{}")
        self.calls.append((req.url.path.rsplit("/", 1)[-1], body))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "username": "cryptonex_bot"}})

    def texts(self):
        return [b.get("text", "") for m, b in self.calls if m == "sendMessage"]


@pytest.fixture
def tg():
    rec = TGRecorder()
    bot.set_client(TelegramClient("123:TEST", transport=httpx.MockTransport(rec)))
    yield rec
    bot.set_client(None)


def tg_post(client, update, secret="whsecret"):
    return client.post("/api/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def msg(text, chat=555):
    return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": chat}, "text": text}}


def cb(data, chat=555):
    return {"update_id": 2, "callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": chat}}}}


def test_telegram_webhook_rejects_bad_secret(client, tg):
    assert tg_post(client, msg("/start"), secret="wrong").status_code == 401
    assert client.post("/api/telegram/webhook", json=msg("/start")).status_code == 401
    assert tg.calls == []


def test_telegram_link_and_trade_flow(client, db, tg):
    h_admin, _ = register(client, "owner@test.io")
    code = client.post("/api/settings/telegram/link", headers=h_admin).json()["code"]
    assert tg_post(client, msg("/signals")).status_code == 200
    assert "not linked" in tg.texts()[-1]
    tg_post(client, msg(f"/start {code}"))
    assert "linked" in tg.texts()[-1]
    s = seed_signal(db)
    tg_post(client, msg("/signals"))
    assert "NEW SPOT SIGNAL" in tg.texts()[-1] and "BTC/USDT" in tg.texts()[-1]
    assert "WAITING FOR USER" in tg.texts()[-1]
    tg_post(client, cb(f"e:{s.id}"))
    assert "actual entry price" in tg.texts()[-1]
    tg_post(client, msg("90010 0.02"))
    assert "ENTERED recorded" in tg.texts()[-1]
    tg_post(client, cb(f"c:{s.id}"))
    tg_post(client, msg("93600 2.5"))
    assert "CLOSED" in tg.texts()[-1]
    db.expire_all()
    u = db.query(User).filter_by(email="owner@test.io").one()
    from app.models import UserTrade
    t = db.query(UserTrade).filter_by(user_id=u.id).one()
    assert t.status == "CLOSED" and t.exit_price == 93600 and t.fees == 2.5
    for cmd in ("/performance", "/agents", "/dashboard", "/settings"):
        tg_post(client, msg(cmd))
    assert "performance" in tg.texts()[-4].lower() and "Agents" in tg.texts()[-3]


async def test_notify_new_signal_and_outcome(client, db, tg):
    h_admin, _ = register(client, "owner@test.io")
    code = client.post("/api/settings/telegram/link", headers=h_admin).json()["code"]
    tg_post(client, msg(f"/start {code}"))
    s = seed_signal(db)
    await bot.notify("new_signal", s)
    m, body = tg.calls[-1]
    assert m == "sendMessage" and body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"e:{s.id}"
    s.theoretical_outcome, s.theoretical_exit_price, s.theoretical_return_pct = "TARGET_HIT", 93600, 4.0
    db.commit()
    await bot.notify("outcome", s)
    assert "TARGET HIT" in tg.texts()[-1]
    await bot.notify("system_alert", "test alert")
    assert "System alert" in tg.texts()[-1]


def test_spa_serving_and_no_path_traversal(tmp_path):
    from fastapi import FastAPI

    from app.main import _mount_spa
    site = tmp_path / "dist"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("<html>SPA</html>")
    (site / "assets" / "app.js").write_text("js")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    a = FastAPI()
    _mount_spa(a, str(site))
    with TestClient(a) as c:
        assert c.get("/").text == "<html>SPA</html>"
        assert c.get("/app/signals/3").text == "<html>SPA</html>"  # client-side route
        assert c.get("/assets/app.js").text == "js"
        for bad in ("/../secret.txt", "/assets/../../secret.txt", "/..%2fsecret.txt", "/%2e%2e/secret.txt"):
            assert "TOP-SECRET" not in c.get(bad).text
        assert c.get("/api/unknown").status_code == 404


def test_postgres_url_normalized():
    from app.config import Settings
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"


def test_base64_webhook_secret_is_made_telegram_safe(client, tg, monkeypatch):
    """Render's generateValue yields base64 (+ / =), which Telegram rejects in secret_token."""
    import hashlib
    import re

    from app.config import get_settings
    raw = "kZ3+8vQ/Ab9=="
    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", raw)
    token = bot.webhook_secret_token()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token) and token == hashlib.sha256(raw.encode()).hexdigest()
    assert tg_post(client, msg("/start"), secret=raw).status_code == 401  # raw value is not what Telegram sends
    assert tg_post(client, msg("/start"), secret=token).status_code == 200


async def test_auto_register_webhook_at_startup(tg, monkeypatch, db):
    from app.config import get_settings
    from app.models import ServiceState
    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", "abc+/=")
    monkeypatch.setattr(get_settings(), "public_base_url", "https://cryptonex.example.com")
    await bot.auto_register_webhook()
    calls = {m: b for m, b in tg.calls}
    assert calls["setWebhook"]["url"] == "https://cryptonex.example.com/api/telegram/webhook"
    assert calls["setWebhook"]["secret_token"] == bot.webhook_secret_token()
    assert db.get(ServiceState, "telegram").status == "ok"
    tg.calls.clear()
    monkeypatch.setattr(get_settings(), "public_base_url", "http://localhost:5173")
    await bot.auto_register_webhook()  # never registers a non-HTTPS URL
    assert tg.calls == []
