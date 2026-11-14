"""Agent registry: static definitions from config/agents.yaml + live state rows in hq_agents."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..config import get_settings
from .models import HQAgent

CONFIG = Path(__file__).parent / "config" / "agents.yaml"
PROMPTS = Path(__file__).parent / "prompts"
MAX_AGENTS = 15
LEVEL_THRESHOLDS = [0, 50, 150, 300, 500, 800, 1200, 1700, 2300, 3000]  # XP needed for Lv1..Lv10


class AgentDef(BaseModel):
    id: str
    name: dict[str, str]
    role: str
    room: str
    model: str = "routine"
    tools: dict[str, int] = {}
    schedule: str = "manual"
    color: str = "#a1a1aa"
    strategy: str | None = None  # primary strategy (kept for compatibility)
    strategies: list[str] = []  # full strategy portfolio owned by this agent

    @property
    def all_strategies(self) -> list[str]:
        out = list(self.strategies)
        if self.strategy and self.strategy not in out:
            out.insert(0, self.strategy)
        return out

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("agent id must be alphanumeric/underscore")
        return v

    def model_name(self) -> str:
        s = get_settings()
        return s.hq_model_senior if self.model == "senior" else s.hq_model_routine

    def prompt(self) -> str:
        p = PROMPTS / f"{self.id}.md"
        return p.read_text(encoding="utf-8") if p.is_file() else f"You are {self.name['en']}. {self.role}"


@functools.lru_cache
def load_defs() -> dict[str, AgentDef]:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    defs = [AgentDef.model_validate(a) for a in raw["agents"]]
    if len(defs) > MAX_AGENTS:
        raise ValueError(f"at most {MAX_AGENTS} agents are supported")
    ids = [d.id for d in defs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate agent id")
    if "manager" not in ids:
        raise ValueError("the manager agent is required")
    return {d.id: d for d in defs}


def get_def(agent_id: str) -> AgentDef:
    return load_defs()[agent_id]


def level_for_xp(xp: int) -> int:
    lvl = 1
    for i, need in enumerate(LEVEL_THRESHOLDS):
        if xp >= need:
            lvl = i + 1
    return min(lvl, 10)


def ensure_agents(db: Session) -> None:
    """Create state rows for agents added to the config (removed agents are just disabled)."""
    defs = load_defs()
    rows = {a.id: a for a in db.query(HQAgent).all()}
    for aid in defs:
        if aid not in rows:
            db.add(HQAgent(id=aid))
        elif not rows[aid].enabled:
            rows[aid].enabled = True
    for aid, row in rows.items():
        if aid not in defs:
            row.enabled = False
    db.commit()


def can_use(db: Session, agent_id: str, tool: str) -> bool:
    d = get_def(agent_id)
    if tool not in d.tools:
        return False
    row = db.get(HQAgent, agent_id)
    return (row.level if row else 1) >= d.tools[tool]


def agent_view(d: AgentDef, row: HQAgent | None) -> dict:
    lvl = row.level if row else 1
    xp = row.xp if row else 0
    nxt = LEVEL_THRESHOLDS[lvl] if lvl < 10 else None
    return {
        "id": d.id, "name": d.name, "role": d.role, "room": d.room, "model": d.model_name(), "color": d.color,
        "schedule": d.schedule, "strategy": d.strategy, "strategies": d.all_strategies,
        "tools": {t: {"min_level": m, "unlocked": lvl >= m} for t, m in d.tools.items()},
        "level": lvl, "xp": xp, "next_level_xp": nxt,
        "status": row.status if row else "idle", "current_task": row.current_task if row else "",
        "last_active_at": row.last_active_at if row else None, "enabled": row.enabled if row else True,
    }
