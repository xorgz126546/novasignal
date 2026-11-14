"""Telegram message formatting (HTML parse mode, escaped)."""
from __future__ import annotations

from html import escape

from ..models import Signal

DISCLAIMER = "<i>Not financial advice. No profit is guaranteed. You decide and execute manually on Binance Spot.</i>"


def fmt_price(p: float | None) -> str:
    if p is None:
        return "—"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.8g}"


def pair(sig: Signal) -> str:
    return f"{sig.base_asset}/{sig.quote_asset}" if sig.base_asset else sig.symbol


def new_signal(sig: Signal, dashboard_url: str) -> str:
    return "\n".join([
        "<b>CRYPTONEX AI</b>", "<b>NEW SPOT SIGNAL</b>", "",
        f"Pair: <b>{escape(pair(sig))}</b>", "Action: <b>BUY</b>", "",
        f"Entry: {fmt_price(sig.entry_price)}", f"Take Profit: {fmt_price(sig.take_profit)}",
        f"Stop Loss: {fmt_price(sig.stop_loss)}",
        *([f"Invalidation: {fmt_price(sig.invalidation_price)}"] if getattr(sig, "invalidation_price", None) else []),
        *([f"Risk: {sig.risk_pct:.2f}% of equity (reference sizing)"] if getattr(sig, "risk_pct", None) else []),
        *([f"Strategy: {escape(sig.strategy_id)} (HQ)"] if getattr(sig, "strategy_id", None) else []), "",
        f"Expected Price Movement: +{sig.expected_move_pct:.2f}%",
        f"Net after est. fees/slippage: +{sig.net_expected_return_pct:.2f}%",
        f"Risk/Reward: 1:{sig.risk_reward:.2f}",
        f"Confidence score: {sig.confidence:.0f}/100 (evidence score, not a win probability)",
        f"Entry valid until: {sig.entry_valid_until:%Y-%m-%d %H:%M} UTC",
        f"Expires: {sig.expires_at:%Y-%m-%d %H:%M} UTC", "",
        "Status: <b>WAITING FOR USER</b>", "",
        f'<a href="{escape(dashboard_url)}/signals/{sig.id}">Open in dashboard</a>', "", DISCLAIMER,
    ])


def signal_keyboard(sig_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ ENTERED", "callback_data": f"e:{sig_id}"},
        {"text": "⏭ SKIPPED", "callback_data": f"s:{sig_id}"},
        {"text": "🏁 CLOSED", "callback_data": f"c:{sig_id}"},
    ]]}


def outcome(sig: Signal) -> str:
    title = {"TARGET_HIT": "🎯 TARGET HIT", "STOP_LOSS_HIT": "🛑 STOP LOSS HIT", "EXPIRED": "⌛ SIGNAL EXPIRED"}.get(
        sig.theoretical_outcome or "", sig.status)
    lines = ["<b>CRYPTONEX AI</b>", f"<b>{title}</b>", "", f"Pair: <b>{escape(pair(sig))}</b> (signal #{sig.id})",
             f"Entry: {fmt_price(sig.entry_price)} → Level: {fmt_price(sig.theoretical_exit_price)}",
             f"Theoretical move: {sig.theoretical_return_pct:+.2f}% (before fees)"]
    if sig.paper_trade and sig.paper_trade.return_pct is not None:
        lines.append(f"Paper result (fees+slippage): {sig.paper_trade.return_pct:+.2f}%")
    lines += ["", "If you entered this trade, tap 🏁 CLOSED and report your actual exit price and fees."]
    return "\n".join(lines)
