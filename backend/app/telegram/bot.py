"""Telegram update handling (webhook or polling) and outbound notifications."""
from __future__ import annotations

import hmac
import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..engine import tracks
from ..engine.lifecycle import LifecycleError, report_closed, report_entered, report_skipped
from ..engine.performance import compute_metrics
from ..engine.services import set_service_state
from ..models import AgentRun, Signal, SignalStatus, TelegramSession, User, UserTrade, utcnow
from ..trading_config import load_config
from . import messages as M
from .client import TelegramClient, TelegramError

log = logging.getLogger("cryptonex.telegram")
_client: TelegramClient | None = None
NUM = r"(\d+(?:\.\d+)?)"


def get_client() -> TelegramClient | None:
    global _client
    s = get_settings()
    if not s.telegram_configured:
        return None
    if _client is None:
        _client = TelegramClient(s.telegram_bot_token, s.telegram_api_base)
    return _client


def set_client(c: TelegramClient | None) -> None:
    global _client
    _client = c


def verify_secret(header_value: str | None) -> bool:
    secret = get_settings().telegram_webhook_secret
    return bool(secret) and header_value is not None and hmac.compare_digest(secret, header_value)


def _session(db: Session, chat_id: str) -> TelegramSession:
    st = db.get(TelegramSession, chat_id)
    if st is None:
        st = TelegramSession(chat_id=chat_id)
        db.add(st)
    return st


def _perf_text(db: Session, user: User) -> str:
    lines = ["<b>CRYPTONEX AI — performance</b>", "<i>Tracks are separate; never combined.</i>", ""]
    for t, label in (("theoretical", "Theoretical (signal levels)"), ("paper", "Paper (fees+slippage)"),
                     ("actual", "Your actual trades")):
        m = compute_metrics(tracks.load(db, t, user.id if t == "actual" else None))
        if m["trades"] == 0:
            lines.append(f"{label}: no closed trades yet")
        else:
            lines.append(f"{label}: {m['trades']} trades, win rate {m['win_rate'] * 100:.0f}%, "
                         f"expectancy {m['expectancy_pct']:+.2f}%/trade, max DD {m['max_drawdown_pct']:.1f}%")
    return "\n".join(lines)


async def handle_update(db: Session, update: dict, client: TelegramClient) -> None:
    s = get_settings()
    if cq := update.get("callback_query"):
        chat_id = str(cq["message"]["chat"]["id"])
        user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
        if not user:
            await client.answer_callback(cq["id"], "Link your account first: /start <code>")
            return
        m = re.fullmatch(r"([esc]):(\d+)", cq.get("data", ""))
        sig = db.get(Signal, int(m.group(2))) if m else None
        if not sig:
            await client.answer_callback(cq["id"], "Unknown signal")
            return
        action = m.group(1)
        if action == "s":
            try:
                report_skipped(db, user, sig)
                await client.answer_callback(cq["id"], "Recorded: SKIPPED")
                await client.send_message(chat_id, f"⏭ Signal #{sig.id} {M.pair(sig)} recorded as SKIPPED.")
            except LifecycleError as e:
                await client.answer_callback(cq["id"], str(e)[:190])
            return
        st = _session(db, chat_id)
        st.awaiting, st.signal_id = ("entry_price" if action == "e" else "exit_price"), sig.id
        db.commit()
        await client.answer_callback(cq["id"])
        if action == "e":
            await client.send_message(chat_id, f"Signal #{sig.id} {M.pair(sig)}: reply with your <b>actual entry price</b> "
                                               "and optionally quantity, e.g. <code>90012.5 0.01</code>")
        else:
            await client.send_message(chat_id, f"Signal #{sig.id} {M.pair(sig)}: reply with <b>exit price</b> and "
                                               "<b>total fees in USDT</b>, e.g. <code>93500 0.18</code>")
        return

    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text or "chat" not in msg:
        return
    chat_id = str(msg["chat"]["id"])
    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            code = parts[1].strip()
            u = db.scalar(select(User).where(User.telegram_link_code == code)) if code else None
            if u:
                other = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
                if other and other.id != u.id:
                    other.telegram_chat_id = None
                u.telegram_chat_id, u.telegram_link_code = chat_id, None
                db.commit()
                await client.send_message(chat_id, "✅ Telegram linked to your CRYPTONEX AI account.\n"
                                                   "Commands: /signals /performance /agents /dashboard /settings")
                return
            await client.send_message(chat_id, "Invalid or expired link code. Generate a new one in Settings → Telegram.")
            return
        await client.send_message(chat_id, "<b>CRYPTONEX AI</b> — intelligent crypto signals.\n"
                                           "To link this chat, open Settings → Telegram in the dashboard and send "
                                           "<code>/start YOUR_CODE</code>.\n\n" + M.DISCLAIMER)
        return
    if not user:
        await client.send_message(chat_id, "This chat is not linked. Use /start <code> from the dashboard Settings page.")
        return

    st = db.get(TelegramSession, chat_id)
    if st and st.awaiting and not text.startswith("/"):
        sig = db.get(Signal, st.signal_id) if st.signal_id else None
        nums = [float(x) for x in re.findall(NUM, text.replace(",", ""))]
        try:
            if not sig or not nums:
                raise LifecycleError("please send numbers only, e.g. 90012.5 0.01")
            risk = load_config(db).risk
            if st.awaiting == "entry_price":
                t = report_entered(db, user, sig, nums[0], nums[1] if len(nums) > 1 else None, risk=risk)
                reply = (f"✅ ENTERED recorded for #{sig.id} {M.pair(sig)} at {M.fmt_price(t.entry_price)} "
                         f"(slippage vs signal {t.slippage_pct:+.3f}%).")
            else:
                fees = nums[1] if len(nums) > 1 else 0.0
                t = report_closed(db, user, sig, nums[0], fees, nums[2] if len(nums) > 2 else None)
                pnl = f", P/L {t.realized_pnl:+.2f} USDT" if t.realized_pnl is not None else ""
                reply = f"🏁 CLOSED #{sig.id}: exit {M.fmt_price(t.exit_price)}, return {t.realized_pct:+.2f}%{pnl}."
            st.awaiting, st.signal_id = None, None
            db.commit()
            await client.send_message(chat_id, reply)
        except LifecycleError as e:
            db.rollback()
            await client.send_message(chat_id, f"⚠️ {e}")
        return

    cmd = text.split()[0].split("@")[0].lower()
    if cmd == "/dashboard":
        await client.send_message(chat_id, f'<a href="{s.public_base_url}">Open CRYPTONEX AI dashboard</a>')
    elif cmd == "/signals":
        sigs = list(db.scalars(select(Signal).where(Signal.status.in_([SignalStatus.ACTIVE.value,
                                                                       SignalStatus.USER_ENTERED.value,
                                                                       SignalStatus.USER_SKIPPED.value]))
                               .order_by(Signal.id.desc()).limit(5)))
        if not sigs:
            await client.send_message(chat_id, "No open signals. Zero signals is normal when no qualified setup exists.")
        for sig in sigs:
            await client.send_message(chat_id, M.new_signal(sig, s.public_base_url), M.signal_keyboard(sig.id))
    elif cmd == "/performance":
        await client.send_message(chat_id, _perf_text(db, user))
    elif cmd == "/agents":
        since = utcnow() - timedelta(hours=24)
        rows = db.execute(select(AgentRun.agent_id, AgentRun.status).where(AgentRun.started_at >= since)).all()
        from collections import Counter
        runs, errs = Counter(r[0] for r in rows), Counter(r[0] for r in rows if r[1] == "error")
        from ..agents import ALL_AGENTS
        lines = ["<b>Agents (last 24h)</b>"] + [f"{a.number:02d} {a.name}: {runs.get(a.id, 0)} runs, {errs.get(a.id, 0)} errors"
                                                for a in ALL_AGENTS]
        await client.send_message(chat_id, "\n".join(lines))
    elif cmd == "/settings":
        prefs = user.notification_prefs or {}
        await client.send_message(chat_id, "Notification preferences are managed in the dashboard (Settings).\n"
                                           f"Current: {prefs or 'defaults (all on)'}\n{s.public_base_url}/settings")
    elif cmd in ("/entered", "/skipped", "/closed"):
        await client.send_message(chat_id, "Use the buttons under a signal (/signals) to report ENTERED, SKIPPED or CLOSED.")
    else:
        await client.send_message(chat_id, "Commands: /signals /performance /agents /dashboard /settings")


def _wants(user: User, key: str) -> bool:
    return (user.notification_prefs or {}).get(key, True)


async def notify(event: str, payload, prev_status: str | None = None) -> None:
    """Outbound notifications. Failures are logged and recorded; they never break the engine."""
    client = get_client()
    if client is None:
        return
    s = get_settings()
    with SessionLocal() as db:
        users = list(db.scalars(select(User).where(User.telegram_chat_id.is_not(None), User.is_active.is_(True))))
        try:
            for u in users:
                if event == "new_signal" and _wants(u, "new_signal"):
                    await client.send_message(u.telegram_chat_id, M.new_signal(payload, s.public_base_url),
                                              M.signal_keyboard(payload.id))
                elif event == "outcome":
                    key = {"TARGET_HIT": "target_hit", "STOP_LOSS_HIT": "stop_loss", "EXPIRED": "expired"}.get(
                        payload.theoretical_outcome, "outcome")
                    if _wants(u, key):
                        sig = db.get(Signal, payload.id)
                        await client.send_message(u.telegram_chat_id, M.outcome(sig))
                elif event == "daily_summary" and _wants(u, "daily_summary"):
                    await client.send_message(u.telegram_chat_id, payload + "\n\n" + _perf_text(db, u))
                elif event == "system_alert" and u.role == "admin" and _wants(u, "system_alerts"):
                    await client.send_message(u.telegram_chat_id, f"⚠️ <b>System alert</b>\n{payload}")
            set_service_state(db, "telegram", ok=True)
        except TelegramError as e:
            log.warning("telegram notify failed: %s", e)
            set_service_state(db, "telegram", ok=False, error=str(e))


async def poll_forever() -> None:  # pragma: no cover - manual dev tool
    """Local development without a public webhook URL: python -m app.telegram.poll"""
    import asyncio

    client = get_client()
    if client is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")
    await client.delete_webhook()
    offset = None
    while True:
        try:
            for upd in await client.get_updates(offset):
                offset = upd["update_id"] + 1
                with SessionLocal() as db:
                    await handle_update(db, upd, client)
        except TelegramError as e:
            log.warning("polling error: %s", e)
            await asyncio.sleep(5)
