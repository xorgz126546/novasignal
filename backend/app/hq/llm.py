"""Budget-capped LLM access for HQ agents (official Anthropic SDK).

- Per-agent model (routine vs senior), daily USD cap tracked in hq_llm_usage.
- Returns None (caller falls back to deterministic templates) when: no key, budget exhausted, error, refusal.
- The LLM only reasons/explains/writes. `check_numbers` verifies every number in its text appears in the facts.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import utcnow
from .models import HQLLMUsage
from .registry import get_def

log = logging.getLogger("cryptonex.hq.llm")

# USD per 1M tokens (input, output). Unknown models are priced conservatively at the highest rate.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
FALLBACK_PRICE = (8.0, 40.0)
_client_factory = None  # tests inject a fake async client factory


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


def cost(model: str, inp: int, out: int) -> float:
    pi, po = PRICES.get(model, FALLBACK_PRICE)
    return inp / 1e6 * pi + out / 1e6 * po


def spent_today(db: Session) -> float:
    since = utcnow() - timedelta(hours=24)
    return float(db.scalar(select(func.coalesce(func.sum(HQLLMUsage.cost_usd), 0.0)).where(HQLLMUsage.at >= since)) or 0.0)


def budget_status(db: Session) -> dict:
    cap = get_settings().hq_llm_daily_budget_usd
    spent = spent_today(db)
    return {"cap_usd": cap, "spent_24h_usd": round(spent, 4), "remaining_usd": round(max(cap - spent, 0.0), 4),
            "available": bool(get_settings().anthropic_api_key) and spent < cap}


def system_prompt(db: Session, agent_id: str) -> str:
    """Approved prompt version if one is active, else the committed seed prompt + current skills."""
    from .versions import active_data

    p = active_data(db, f"prompt:{agent_id}")
    base = p.get("text") if p else get_def(agent_id).prompt()
    skills = active_data(db, f"skills:{agent_id}")
    if skills and skills.get("text"):
        base += "\n\n## Your skills file (lessons learned, approved)\n" + skills["text"][:6000]
    return base


def _make_client():
    if _client_factory:
        return _client_factory()
    import anthropic

    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key, timeout=60.0, max_retries=2)


async def complete(db: Session, agent_id: str, user: str, purpose: str, max_tokens: int = 1500,
                   json_mode: bool = False) -> str | None:
    s = get_settings()
    if not s.anthropic_api_key and not _client_factory:
        return None
    if spent_today(db) >= s.hq_llm_daily_budget_usd:
        log.info("LLM budget exhausted; %s falls back to templates", agent_id)
        return None
    d = get_def(agent_id)
    model = d.model_name()
    worst = cost(model, len(user) // 3 + 2000, max_tokens)
    if spent_today(db) + worst > s.hq_llm_daily_budget_usd:
        return None
    kwargs = dict(model=model, max_tokens=max_tokens, system=system_prompt(db, agent_id),
                  messages=[{"role": "user", "content": user}], output_config={"effort": "low"})
    if json_mode:
        kwargs["system"] += "\n\nRespond with ONE JSON object only. No prose, no code fences."
    ok, text, inp, out = True, None, 0, 0
    try:
        client = _make_client()
        if model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            resp = await client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = await client.messages.create(**kwargs)
        inp, out = resp.usage.input_tokens, resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            ok = False
        else:
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip() or None
    except Exception as e:
        log.warning("LLM call failed for %s: %s", agent_id, e)
        ok = False
    db.add(HQLLMUsage(agent_id=agent_id, model=model, input_tokens=inp, output_tokens=out,
                      cost_usd=cost(model, inp, out), purpose=purpose, ok=ok))
    db.commit()
    return text


async def complete_json(db: Session, agent_id: str, user: str, purpose: str, max_tokens: int = 2000) -> dict | None:
    text = await complete(db, agent_id, user, purpose, max_tokens, json_mode=True)
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except (ValueError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


_NUM = re.compile(r"(?<![A-Za-z_])[-+]?\d[\d,]*\.?\d*")


def _norm(x: str) -> str:
    x = x.replace(",", "").lstrip("+")
    try:
        f = float(x)
    except ValueError:
        return x
    return f"{f:.6g}"


def check_numbers(text: str, facts) -> list[str]:
    """Numbers in LLM text that do not appear in the facts (small integers ≤ 10 are allowed for counting words)."""
    blob = json.dumps(facts, default=str)
    allowed = {_norm(n) for n in _NUM.findall(blob)}
    # also allow common roundings of fact numbers (to 0-4 decimals)
    for n in list(_NUM.findall(blob)):
        try:
            f = float(n.replace(",", ""))
        except ValueError:
            continue
        for dgt in range(0, 5):
            allowed.add(f"{round(f, dgt):.6g}")
    bad = []
    for n in _NUM.findall(text):
        v = _norm(n)
        try:
            if abs(float(v)) <= 10 and float(v) == int(float(v)):
                continue
        except ValueError:
            pass
        if v not in allowed:
            bad.append(n)
    return bad


def facts_block(facts) -> str:
    return "FACTS (the only numbers you may use):\n" + json.dumps(facts, default=str, ensure_ascii=False)[:12000]


async def write_text(db: Session, agent_id: str, instruction: str, facts, purpose: str, fallback: str,
                     max_tokens: int = 1200) -> tuple[str, bool]:
    """LLM writes prose from facts; if it invents a number or is unavailable, the deterministic fallback is used.
    Returns (text, used_llm)."""
    text = await complete(db, agent_id, f"{instruction}\n\n{facts_block(facts)}", purpose, max_tokens)
    if text and not check_numbers(text, facts):
        return text, True
    if text:
        log.warning("%s produced unverified numbers; using deterministic text", agent_id)
    return fallback, False

