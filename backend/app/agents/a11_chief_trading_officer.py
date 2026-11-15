"""AGENT 11 — CHIEF TRADING OFFICER: final approve/reject, rule enforcement, de-duplication, confidence model.

CONFIDENCE MODEL (documented in docs/CONFIDENCE_MODEL.md; deterministic and unit-tested):

    confidence = 100 * Σ w_a · score_a       over the six evidence agents below
        technical_analyst 0.20 | trend_detector 0.20 | volume_intelligence 0.15
        support_resistance 0.15 | price_action 0.15 | strategy_validator 0.15

Each score_a ∈ [0,1] is the agent's own documented evidence score. News & Sentiment does NOT add
confidence (data is often unavailable); it can only veto. Confidence is an evidence-strength
score, NOT a probability of profit, and is reported as such.
"""
from __future__ import annotations

from pydantic import BaseModel

from .base import AgentInput, AgentOutput, BaseAgent

CONFIDENCE_WEIGHTS = {
    "technical_analyst": 0.20,
    "trend_detector": 0.20,
    "volume_intelligence": 0.15,
    "support_resistance": 0.15,
    "price_action": 0.15,
    "strategy_validator": 0.15,
}
MANDATORY = ("technical_analyst", "trend_detector", "volume_intelligence", "support_resistance", "price_action",
             "news_sentiment", "entry_exit", "risk_manager", "strategy_validator")


def confidence_score(results: dict[str, dict]) -> tuple[float, dict[str, float]]:
    breakdown = {a: round(w * float(results.get(a, {}).get("score", 0.0)) * 100, 2) for a, w in CONFIDENCE_WEIGHTS.items()}
    return round(sum(breakdown.values()), 2), breakdown


class Dossier(BaseModel):
    symbol: str
    results: dict[str, dict]  # agent_id -> output dict


class ChiefInput(AgentInput):
    dossiers: list[Dossier]
    paused: bool = False
    signals_today: int = 0
    max_signals_per_day: int = 3
    active_symbols: list[str] = []  # symbols with an open signal
    cooldown_symbols: list[str] = []  # symbols signalled within cooldown window
    min_confidence: float = 60.0


class Decision(BaseModel):
    symbol: str
    approved: bool
    confidence: float
    breakdown: dict[str, float]
    rejection_reasons: list[str]
    justification: list[str]


class ChiefOutput(AgentOutput):
    decisions: list[Decision] = []
    approved_symbols: list[str] = []


class ChiefTradingOfficer(BaseAgent[ChiefInput, ChiefOutput]):
    id = "chief_trading_officer"
    number = 11
    name = "Chief Trading Officer"
    responsibility = "Coordinate agents, resolve disagreements, enforce rules/limits, de-duplicate, approve or reject, keep audit trail."
    Input = ChiefInput
    Output = ChiefOutput

    def execute(self, i: ChiefInput) -> ChiefOutput:
        decisions: list[Decision] = []
        for d in i.dossiers:
            r = d.results
            reasons: list[str] = []
            if i.paused:
                reasons.append("signal generation paused by admin")
            for a in MANDATORY:
                res = r.get(a)
                if res is None:
                    reasons.append(f"{a}: no output")
                elif res.get("error"):
                    reasons.append(f"{a}: error — {res['error']}")
                elif not res.get("passed"):
                    first = next((x for x in res.get("reasons", []) if x), "did not pass")
                    reasons.append(f"{a}: rejected ({first})")
            if d.symbol in i.active_symbols:
                reasons.append("duplicate: symbol already has an open signal")
            if d.symbol in i.cooldown_symbols:
                reasons.append("symbol in post-signal cooldown")
            conf, breakdown = confidence_score(r)
            if conf < i.min_confidence:
                reasons.append(f"confidence {conf:.1f} below minimum {i.min_confidence:.0f}")
            just = []
            for a in ("trend_detector", "technical_analyst", "support_resistance", "volume_intelligence", "price_action",
                      "strategy_validator", "entry_exit", "risk_manager", "news_sentiment"):
                rs = r.get(a, {}).get("reasons", [])
                if rs:
                    just.append(f"[{a}] " + "; ".join(rs[:3]))
            decisions.append(Decision(symbol=d.symbol, approved=not reasons, confidence=conf, breakdown=breakdown,
                                      rejection_reasons=reasons, justification=just))
        # Daily quota + no contradictory/duplicate approvals within a cycle: rank by confidence.
        quota = max(i.max_signals_per_day - i.signals_today, 0)
        seen: set[str] = set()
        for dec in sorted([x for x in decisions if x.approved], key=lambda x: -x.confidence):
            if dec.symbol in seen:
                dec.approved = False
                dec.rejection_reasons.append("duplicate within cycle")
            elif quota <= 0:
                dec.approved = False
                dec.rejection_reasons.append(f"daily signal limit reached ({i.max_signals_per_day})")
            else:
                quota -= 1
                seen.add(dec.symbol)
        approved = [d.symbol for d in decisions if d.approved]
        return ChiefOutput(passed=bool(approved), score=1.0 if approved else 0.0, decisions=decisions,
                           approved_symbols=approved,
                           reasons=[f"approved {len(approved)} of {len(decisions)} candidates"]
                           + (["no qualified opportunity this cycle — zero signals is a valid outcome"] if not approved else []))
