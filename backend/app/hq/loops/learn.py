"""Loop E — Learn from real outcomes.

When a published signal closes (TARGET_HIT or STOP_LOSS_HIT), the team:
1. rebuilds the exact market fingerprint at the moment the signal was sent (real Binance candles, closed bars
   before the signal time only — no look-ahead);
2. stores it as a knowledge note (winners AND losers, so we don't learn only from survivors);
3. for a winner, turns the fingerprint into a "pattern twin" rule strategy (safe JSON DSL, scale-free features only);
4. runs the twin through the Backtester walk-forward gate on real history (fees, slippage, out-of-sample,
   overfitting penalty). One winning trade proves nothing; only the gate decides;
5. if it passes, it goes to "Needs you". Nothing goes live without the owner's approval.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient, MarketDataError, get_client
from ...models import Signal, SignalStatus
from .. import bus, runtime
from ..features import build_features
from ..models import HQKnowledge, HQProposal
from ..reports import md_to_telegram, save_report
from ..strategies.rules import RuleSpec

log = logging.getLogger("cryptonex.hq.learn")
INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
BARS = 300
OUTCOMES = (SignalStatus.TARGET_HIT.value, SignalStatus.STOP_LOSS_HIT.value)
# Scale-free features that describe the market state (never raw prices).
FINGERPRINT = ["rsi_1h", "adx_1h", "bb_pos_1h", "ret_24_1h", "atr_pct_1h", "vol_rel_1h", "rsi_4h", "adx_4h",
               "ret_168_1h", "dist_ema21_pct_1h", "bias_1h", "bias_4h"]
TRENDS = [("close_1h", "ema50_1h"), ("ema21_4h", "ema50_4h"), ("close_4h", "ema200_4h")]


def _slug(sig: Signal) -> str:
    return f"outcome-signal-{sig.id}"


def _outcome(sig: Signal) -> str | None:
    o = sig.theoretical_outcome or sig.status
    return o if o in OUTCOMES else None


async def candles_at(client: BinanceClient, symbol: str, at_ms: int) -> dict:
    """Closed candles that ended BEFORE the signal time, per timeframe."""
    out = {}
    for tf, ms in INTERVAL_MS.items():
        cs = await client.klines_since(symbol, tf, at_ms - BARS * ms, BARS)
        out[tf] = [c for c in cs if c.close_time < at_ms]
    return out


def fingerprint(features: dict) -> dict:
    fp = {k: round(features[k], 4) for k in FINGERPRINT if k in features}
    for a, b in TRENDS:
        if a in features and b in features:
            fp[f"{a}>{b}"] = features[a] > features[b]
    return fp


def _band(lo: float, hi: float, left: str) -> list[dict]:
    return [{"left": left, "op": ">=", "right": round(lo, 4)}, {"left": left, "op": "<=", "right": round(hi, 4)}]


def twin_spec(fp: dict, name: str) -> RuleSpec | None:
    """A 'pattern twin': the same trend structure + bands around the winner's momentum/volatility (max 12 conditions)."""
    cond: list[dict] = []
    for a, b in TRENDS:
        k = f"{a}>{b}"
        if k in fp:
            cond.append({"left": a, "op": ">" if fp[k] else "<", "right": b})
    if "rsi_1h" in fp:
        cond += _band(fp["rsi_1h"] - 8, fp["rsi_1h"] + 8, "rsi_1h")
    if "adx_1h" in fp:
        cond.append({"left": "adx_1h", "op": ">=", "right": round(max(fp["adx_1h"] - 8, 10), 4)})
    if "bb_pos_1h" in fp:
        cond += _band(fp["bb_pos_1h"] - 0.25, fp["bb_pos_1h"] + 0.25, "bb_pos_1h")
    if "ret_24_1h" in fp:
        w = max(2.0, abs(fp["ret_24_1h"]) * 0.5)
        cond += _band(fp["ret_24_1h"] - w, fp["ret_24_1h"] + w, "ret_24_1h")
    if "atr_pct_1h" in fp:
        cond += _band(fp["atr_pct_1h"] * 0.6, fp["atr_pct_1h"] * 1.6, "atr_pct_1h")
    cond = cond[:12]
    if len(cond) < 4:
        return None
    return RuleSpec.model_validate({
        "name": name[:80], "description": "Pattern twin of a real winning signal: same trend structure, similar momentum "
                                          "and volatility. Must pass the walk-forward gate and the owner's approval.",
        "conditions": cond, "stop": {"type": "atr", "tf": "1h", "mult": 1.5}, "quality": 0.65})


def _note_md(sig: Signal, outcome: str, fp: dict, spec: RuleSpec | None) -> str:
    lines = [f"# Lesson from signal #{sig.id} {sig.symbol} — {outcome}", "",
             f"- Strategy: {sig.strategy_id or 'classic engine'}",
             f"- Entry {sig.entry_price} · TP {sig.take_profit} · SL {sig.stop_loss}",
             f"- Result (levels, before fees): {sig.theoretical_return_pct if sig.theoretical_return_pct is not None else 'n/a'}%",
             "", "## Market fingerprint at signal time (closed candles only)",
             *[f"- {k}: {v}" for k, v in fp.items()]]
    if spec:
        lines += ["", "## Pattern twin tested by the Backtester", *[f"- {c.left} {c.op} {c.right}" for c in spec.conditions]]
    lines += ["", "_One trade is not evidence. Only the walk-forward gate on real history counts. Not financial advice._"]
    return "\n".join(lines)


async def learn_from_outcomes(db: Session, client: BinanceClient | None = None, notify_owner=None,
                              signal_id: int | None = None, max_signals: int = 5) -> dict:
    from .upgrade import process_proposal

    client = client or get_client()
    q = select(Signal).order_by(Signal.id.desc())
    if signal_id:
        q = q.where(Signal.id == signal_id)
    todo = [s for s in db.scalars(q.limit(200)) if _outcome(s)
            and not db.scalar(select(HQKnowledge.id).where(HQKnowledge.slug == _slug(s)))][:max_signals]
    results = []
    for sig in todo:
        outcome = _outcome(sig)
        win = outcome == SignalStatus.TARGET_HIT.value
        with runtime.task(db, "librarian", "learn", f"Studying signal #{sig.id} {sig.symbol} ({outcome})") as t:
            try:
                candles = await candles_at(client, sig.symbol, int(sig.created_at.timestamp() * 1000))
            except MarketDataError as e:
                t.result = {"summary": f"market data unavailable: {e}"}
                continue
            feats, _ = build_features(candles)
            fp = fingerprint(feats)
            spec = twin_spec(fp, f"Twin of #{sig.id} {sig.symbol} winner") if win else None
            k = HQKnowledge(slug=_slug(sig), source_url=f"signal:{sig.id}", license="internal", license_class="adaptable",
                            title=f"{'Winner' if win else 'Loser'}: signal #{sig.id} {sig.symbol}",
                            idea=f"Fingerprint of a real {outcome}: {fp}"[:1500],
                            how_to_test="Pattern twin → walk-forward gate" if spec else "Contrast with winners",
                            body_md=_note_md(sig, outcome, fp, spec), status="noted")
            db.add(k)
            db.commit()
            res = {"signal_id": sig.id, "symbol": sig.symbol, "outcome": outcome, "fingerprint": fp, "status": "noted"}
            t.result = {"summary": f"fingerprint stored ({len(fp)} features)"}
        if spec:
            p = HQProposal(agent_id="librarian", kind="rule_strategy", title=spec.name,
                           spec={"rule_spec": spec.model_dump(), "slug": f"twin-{sig.id}"}, knowledge_id=k.id)
            db.add(p)
            k.status = "proposed"
            db.commit()
            out = await process_proposal(db, client, p)
            res.update(status=out["status"], proposal_id=p.id, gate=p.gate)
        bus.publish(db, "lesson", "librarian", signal_id=sig.id, outcome=outcome, status=res["status"])
        results.append(res)
    if results:
        body = summary_md(results)
        save_report(db, "lessons", f"Lessons from {len(results)} closed signal(s)", body, {"results": results}, "librarian")
        if notify_owner:
            await notify_owner(md_to_telegram(body))
    return {"studied": len(results), "results": results}


def summary_md(results: list[dict]) -> str:
    lines = ["# 🎓 الفريق تعلّم من النتائج الحقيقية", ""]
    for r in results:
        g = (r.get("gate") or {}).get("summary") or {}
        head = f"- #{r['signal_id']} {r['symbol']}: {'✅ ربحت' if r['outcome'] == 'TARGET_HIT' else '🛑 خسرت'}"
        if r["status"] == "awaiting_approval":
            head += (f" → نمط مشابه نجح في اختبار التاريخ ({g.get('trades')} صفقة، متوسط {g.get('expectancy_pct')}%/صفقة بعد العمولات)."
                     " ينتظر موافقتك في «Needs you».")
        elif r["status"] == "failed":
            head += (f" → نمط مشابه اختُبر على التاريخ ولم ينجح ({g.get('trades', 0)} صفقة، "
                     f"متوسط {g.get('expectancy_pct')}%). الربح كان حظًا أكثر منه نمطًا متكررًا — لن يُفعّل.")
        else:
            head += " → حُفظت بصمة السوق للمقارنة مع الصفقات الرابحة."
        lines.append(head)
    lines += ["", "_صفقة واحدة ليست دليلًا. لا ربح مضمون. ليست نصيحة مالية._"]
    return "\n".join(lines)
