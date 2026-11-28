"""New built-in strategies that start OFF: the Backtester tests them on real history and the owner decides on Telegram."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient
from .. import approvals, versions
from ..agents.backtester import run_gate
from ..models import HQApproval
from ..strategies.builtin import BUILTINS
from ..strategies.registry import strategy_agents


async def ensure_enable_requests(db: Session, client: BinanceClient) -> list[int]:
    """One approval request per new strategy (ever): gate on real data → Telegram card with the honest numbers."""
    created = []
    owners = strategy_agents()
    for sid, strat in BUILTINS.items():
        if not getattr(strat, "requires_approval", False) or sid not in owners:
            continue
        ref = f"enable:{sid}"
        if db.scalar(select(HQApproval.id).where(HQApproval.ref == ref)):
            continue
        run = await run_gate(db, client, strat, strat.default_params, f"strategy:{sid}@enable")
        r = run.result
        s, oos = r.get("summary") or {}, (r.get("walk_forward") or {}).get("oos") or {}
        v = versions.create_candidate(db, f"strategy:{sid}", {"enabled": True, "params": {}}, owners[sid],
                                      note=f"enable {strat.name}")
        db.commit()
        verdict = ("نجحت في كل فحوص البوابة." if r.get("passed") else
                   "لم تنجح في كل الفحوص: " + "، ".join(k for k, ok in (r.get("checks") or {}).items() if not ok) + ".")
        summary = (f"استراتيجية جديدة: {strat.name}. على بيانات Binance الحقيقية مع العمولات: {s.get('trades')} صفقة، "
                   f"نسبة الربح {round((s.get('win_rate') or 0) * 100)}%، متوسط {round(s.get('expectancy_pct') or 0, 2)}% للصفقة. "
                   f"الفترة الأخيرة غير المختبرة: {oos.get('trades')} صفقة، {round(oos.get('expectancy_pct') or 0, 2)}% للصفقة. "
                   f"{verdict} إذا وافقت تبدأ تعطي توصيات من نوعها (مع كل قواعد المخاطر)، ويمكنك إيقافها بضغطة. ليست نصيحة مالية.")
        a = approvals.request(db, "config", f"[new strategy] {strat.name}", summary,
                              {"version_id": v.id, "key": f"strategy:{sid}", "evidence": "passed" if r.get("passed") else "promising",
                               "gate": {"checks": r.get("checks"), "summary": s}}, [owners[sid], "backtester"], ref=ref)
        created.append(a.id)
    return created
