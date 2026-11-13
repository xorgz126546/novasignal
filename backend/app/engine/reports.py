"""30-day validation program: daily / weekly / final reports.

Three tracks are reported side by side and NEVER merged. The verdict is deliberately strict:
a handful of winners does not make an edge.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Opportunity, Signal, UserTrade, ValidationProgram
from . import tracks
from .performance import bucket, compute_metrics, json_safe

MIN_SAMPLE = 30


def verdict(paper: dict) -> dict:
    n = paper["trades"]
    if n < MIN_SAMPLE:
        return {"code": "INCONCLUSIVE", "text": f"Sample too small: {n} closed paper trades (< {MIN_SAMPLE}). "
                "No conclusion about a trading edge can be drawn."}
    ci = paper.get("expectancy_ci95_pct") or [None, None]
    pf = paper.get("profit_factor")
    pf_ok = pf == "inf" or (pf is not None and pf >= 1.2)
    if paper["expectancy_pct"] > 0 and pf_ok and ci[0] is not None and ci[0] > 0 and paper["max_drawdown_pct"] < 15:
        return {"code": "EDGE_INDICATED", "text": "Positive net expectancy with 95% CI above zero, PF >= 1.2 and drawdown "
                "< 15% after fees/slippage. This indicates (does not prove) an edge; continue forward testing."}
    return {"code": "NO_EDGE_DEMONSTRATED", "text": "Net-of-cost results do not meet the edge criteria "
            "(expectancy CI > 0, profit factor >= 1.2, drawdown < 15%)."}


def build_report(db: Session, start: datetime, end: datetime, kind: str, user_id: int | None = None) -> dict:
    opps = list(db.scalars(select(Opportunity).where(Opportunity.created_at >= start, Opportunity.created_at < end)))
    reasons = Counter()
    for o in opps:
        if o.decision == "rejected":
            for r in o.rejection_reasons[:3]:
                reasons[r.split(" (")[0].split(":")[0]] += 1
    sigs = list(db.scalars(select(Signal).where(Signal.created_at >= start, Signal.created_at < end)))
    sig_ids = [s.id for s in sigs]
    ut_q = select(UserTrade).where(UserTrade.signal_id.in_(sig_ids)) if sig_ids else None
    uts = list(db.scalars(ut_q)) if ut_q is not None else []
    if user_id is not None:
        uts = [u for u in uts if u.user_id == user_id]
    track_data = {}
    for t in ("theoretical", "paper", "actual"):
        rows = tracks.load(db, t, user_id if t == "actual" else None, start, end)
        track_data[t] = {"metrics": json_safe(compute_metrics(rows)), "daily": bucket(rows, "day")}
    rep = {
        "kind": kind, "period_start": start.isoformat(), "period_end": end.isoformat(),
        "opportunities": {"evaluated": len(opps), "approved": sum(1 for o in opps if o.decision == "approved"),
                          "rejected": sum(1 for o in opps if o.decision == "rejected"),
                          "top_rejection_reasons": reasons.most_common(8)},
        "signals": {"generated": len(sigs),
                    "by_status": dict(Counter(s.status for s in sigs)),
                    "executed_by_user": sum(1 for u in uts if u.decision == "ENTERED"),
                    "skipped_by_user": sum(1 for u in uts if u.decision == "SKIPPED"),
                    "no_user_response": len(sigs) - len({u.signal_id for u in uts}),
                    "avg_entry_slippage_pct": (sum(u.slippage_pct for u in uts if u.slippage_pct is not None)
                                               / max(1, sum(1 for u in uts if u.slippage_pct is not None)))},
        "signal_list": [{"id": s.id, "symbol": s.symbol, "created_at": s.created_at.isoformat(), "entry": s.entry_price,
                         "take_profit": s.take_profit, "stop_loss": s.stop_loss, "status": s.status,
                         "theoretical_outcome": s.theoretical_outcome, "theoretical_return_pct": s.theoretical_return_pct,
                         "paper_return_pct": s.paper_trade.return_pct if s.paper_trade else None} for s in sigs],
        "tracks": track_data,
        "disclaimer": "Past and simulated performance does not guarantee future results. Theoretical, paper and "
                      "actual results are reported separately and must not be combined.",
    }
    if kind == "final":
        rep["verdict"] = verdict(track_data["paper"]["metrics"])
    return rep


def program_window(p: ValidationProgram, kind: str, day: datetime | None = None) -> tuple[datetime, datetime]:
    if kind == "final":
        return p.started_at, p.ends_at
    day = day or datetime.now(p.started_at.tzinfo)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "weekly":
        start = start - timedelta(days=start.weekday())
        return start, start + timedelta(days=7)
    return start, start + timedelta(days=1)


def _fmt(v, pct=False):
    if v is None:
        return "n/a"
    if isinstance(v, str):
        return v
    return f"{v:+.2f}%" if pct else f"{v:.2f}"


def to_markdown(rep: dict) -> str:
    L = [f"# CRYPTONEX AI — {rep['kind'].capitalize()} validation report",
         f"Period: {rep['period_start']} → {rep['period_end']}", ""]
    o, s = rep["opportunities"], rep["signals"]
    L += ["## Opportunities", f"- Evaluated: {o['evaluated']} | Approved: {o['approved']} | Rejected: {o['rejected']}"]
    L += [f"  - {r}: {c}" for r, c in o["top_rejection_reasons"]]
    L += ["", "## Signals", f"- Generated: {s['generated']}", f"- Executed by user: {s['executed_by_user']}",
          f"- Skipped by user: {s['skipped_by_user']}", f"- No user response: {s['no_user_response']}",
          f"- Status breakdown: {s['by_status']}", ""]
    L += ["## Performance (tracks are separate — never combine)", "",
          "| Metric | Theoretical | Paper (fees+slippage) | Actual (user-confirmed) |", "|---|---|---|---|"]
    rows = [("Closed trades", "trades", False), ("Win rate", "win_rate", False), ("Expectancy / trade", "expectancy_pct", True),
            ("Avg win", "avg_win_pct", True), ("Avg loss", "avg_loss_pct", True), ("Profit factor", "profit_factor", False),
            ("Max drawdown", "max_drawdown_pct", False), ("Sum of returns", "sum_return_pct", True),
            ("Fees (USDT)", "total_fees", False), ("Slippage cost (USDT)", "total_slippage_cost", False)]
    for label, key, pct in rows:
        vals = []
        for t in ("theoretical", "paper", "actual"):
            v = rep["tracks"][t]["metrics"].get(key)
            vals.append(f"{v * 100:.1f}%" if key == "win_rate" and v is not None else
                        f"{v:.2f}%" if key == "max_drawdown_pct" and v is not None else _fmt(v, pct))
        L.append(f"| {label} | " + " | ".join(vals) + " |")
    if "verdict" in rep:
        L += ["", "## Verdict", f"**{rep['verdict']['code']}** — {rep['verdict']['text']}"]
    L += ["", f"_{rep['disclaimer']}_"]
    return "\n".join(L)
