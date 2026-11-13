"""Optional LLM interpretation layer for the Chief Trading Officer.

The LLM NEVER produces or changes numbers (entry/TP/SL/confidence are deterministic). It only
writes a short plain-language explanation from the structured agent outputs. If no API key is
configured, or the call fails/refuses, the signal is published without a narrative.
"""
from __future__ import annotations

import json
import logging

from ..config import get_settings

log = logging.getLogger("cryptonex.llm")

SYSTEM = (
    "You are the reporting assistant of a crypto spot-signal system. Write a concise (max 90 words) "
    "plain-English explanation of why the deterministic agents approved this BUY signal, using ONLY the "
    "facts provided. Do not add numbers that are not in the input, do not predict profits, do not use the "
    "words 'guaranteed' or 'sure'. End with: 'Not financial advice; you decide whether to trade.'"
)


async def generate_narrative(facts: dict) -> str | None:
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed; skipping narrative")
        return None
    try:
        client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, timeout=30.0, max_retries=2)
        resp = await client.beta.messages.create(
            model=s.anthropic_model,
            max_tokens=1024,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)[:12000]}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None
    except Exception as e:  # never block a signal on the LLM
        log.warning("narrative generation failed: %s", e)
        return None
