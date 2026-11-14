"""Strategy plug-in registry: built-ins (with approved parameter overrides) + approved rule strategies."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..registry import load_defs
from ..versions import active_by_prefix, active_data
from .base import Strategy
from .builtin import BUILTINS
from .rules import RuleSpec, RuleStrategy


@dataclass
class ActiveStrategy:
    strategy: Strategy
    params: dict
    agent_id: str  # agent credited with this strategy's outcomes (XP)


def strategy_agents() -> dict[str, str]:
    return {sid: d.id for d in load_defs().values() for sid in d.all_strategies}


def active_strategies(db: Session) -> list[ActiveStrategy]:
    owners = strategy_agents()
    out: list[ActiveStrategy] = []
    for sid, strat in BUILTINS.items():
        if sid not in owners:
            continue  # strategy agent removed from config → plug-in off
        override = active_data(db, f"strategy:{sid}") or {}
        if override.get("enabled", True) is False:
            continue
        out.append(ActiveStrategy(strat, {**strat.default_params, **override.get("params", {})}, owners[sid]))
    for v in active_by_prefix(db, "rule_strategy:"):
        spec = RuleSpec.model_validate(v.data["spec"])
        out.append(ActiveStrategy(RuleStrategy(v.key.split(":", 1)[1], spec), {}, v.data.get("owner_agent", "scout")))
    return out


def get(sid: str, db: Session | None = None) -> ActiveStrategy | None:
    if sid in BUILTINS:
        owners = strategy_agents()
        override = (active_data(db, f"strategy:{sid}") if db else None) or {}
        return ActiveStrategy(BUILTINS[sid], {**BUILTINS[sid].default_params, **override.get("params", {})},
                              owners.get(sid, "strat_pullback"))
    return None
