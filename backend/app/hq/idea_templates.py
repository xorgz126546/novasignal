"""Deterministic 'idea → testable rule' library used by the Opportunity Researcher.

When a public repo's README talks about a known technique, we do NOT copy its code: we map the technique to OUR
own rule template (safe JSON DSL over our own features). This works without any LLM. Each template is only a
hypothesis; the walk-forward gate on real Binance history decides, then the owner.
"""
from __future__ import annotations

import re

from .strategies.rules import RuleSpec


def _c(left, op, right):
    return {"left": left, "op": op, "right": right}


UPTREND_4H = [_c("close_4h", ">", "ema50_4h"), _c("ema21_4h", ">", "ema50_4h")]

TEMPLATES: list[dict] = [
    {"id": "supertrend_flip", "rx": r"super\s?trend", "name": "Supertrend flip with 4H trend",
     "conditions": [_c("supertrend_flip_up_recent_1h", ">=", 1.0), _c("supertrend_dir_4h", ">=", 1.0), *UPTREND_4H,
                    _c("adx_1h", ">=", 18.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.5}},
    {"id": "stoch_rsi_pullback", "rx": r"stoch(astic)?[\s_-]?rsi|stochrsi", "name": "Stoch-RSI pullback in uptrend",
     "conditions": [*UPTREND_4H, _c("stoch_rsi_d_1h", "<=", 30.0), _c("stoch_rsi_k_1h", ">", "stoch_rsi_d_1h"),
                    _c("rsi_1h", ">=", 40.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.6}},
    {"id": "vwap_reclaim", "rx": r"\bvwap\b", "name": "VWAP reclaim with momentum",
     "conditions": [*UPTREND_4H, _c("vwap_dist_pct_1h", ">=", 0.0), _c("vwap_dist_pct_1h", "<=", 0.8),
                    _c("rsi_1h", ">=", 50.0), _c("rsi_1h", "<=", 66.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.5}},
    {"id": "obv_accumulation", "rx": r"\bobv\b|on[\s-]balance[\s-]volume", "name": "OBV accumulation in trend",
     "conditions": [_c("obv_slope_1h", ">=", 0.25), _c("close_1h", ">", "ema21_1h"), _c("ema21_1h", ">", "ema50_1h"),
                    _c("close_4h", ">", "ema50_4h"), _c("ret_24_1h", "<=", 4.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.5}},
    {"id": "mfi_dip", "rx": r"\bmfi\b|money[\s-]flow[\s-]index", "name": "Money-flow dip in long-term uptrend",
     "conditions": [_c("close_4h", ">", "ema200_4h"), _c("mfi_1h", "<=", 30.0), _c("rsi_change3_1h", ">", 0.0)],
     "stop": {"type": "swing_low", "tf": "1h", "mult": 1.0, "lookback": 10}},
    {"id": "cci_momentum", "rx": r"\bcci\b|commodity[\s-]channel", "name": "CCI momentum breakout",
     "conditions": [_c("cci_1h", ">=", 100.0), _c("cci_4h", ">=", 0.0), *UPTREND_4H, _c("adx_1h", ">=", 20.0),
                    _c("rsi_1h", "<=", 72.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.6}},
    {"id": "williams_r_dip", "rx": r"williams\s?%?\s?r\b|williams[\s-]percent", "name": "Williams %R dip in uptrend",
     "conditions": [*UPTREND_4H, _c("willr_1h", "<=", -80.0), _c("rsi_change3_1h", ">", 0.0)],
     "stop": {"type": "swing_low", "tf": "1h", "mult": 1.0, "lookback": 8}},
    {"id": "ichimoku_tk", "rx": r"ichimoku", "name": "Ichimoku TK cross above the cloud",
     "conditions": [_c("ichi_above_cloud_4h", ">=", 1.0), _c("ichi_above_cloud_1h", ">=", 1.0),
                    _c("ichi_tk_cross_up_recent_1h", ">=", 1.0)], "stop": {"type": "swing_low", "tf": "1h", "mult": 1.0, "lookback": 10}},
    {"id": "ttm_squeeze_release", "rx": r"keltner|ttm[\s-]?squeeze|squeeze[\s-]momentum", "name": "TTM squeeze release up",
     "conditions": [_c("ttm_release_1h", ">=", 1.0), _c("macd_hist_1h", ">", 0.0), _c("close_4h", ">", "ema50_4h"),
                    _c("kc_pos_1h", ">=", 0.6)], "stop": {"type": "atr", "tf": "1h", "mult": 1.5}},
    # core-only ideas (no new tool needed)
    {"id": "rsi_mean_reversion", "rx": r"mean[\s-]reversion|rsi[\s-](oversold|divergence|strategy)",
     "name": "RSI oversold snap-back above EMA200",
     "conditions": [_c("close_4h", ">", "ema200_4h"), _c("rsi_min6_1h", "<=", 32.0), _c("rsi_change3_1h", ">=", 5.0)],
     "stop": {"type": "swing_low", "tf": "1h", "mult": 1.0, "lookback": 8}},
    {"id": "macd_dual_tf", "rx": r"\bmacd\b", "name": "MACD aligned on 1H and 4H",
     "conditions": [_c("macd_hist_1h", ">", 0.0), _c("macd_hist_4h", ">", 0.0), _c("close_4h", ">", "ema50_4h"),
                    _c("rsi_1h", "<=", 68.0), _c("dist_ema21_pct_1h", "<=", 1.5)], "stop": {"type": "atr", "tf": "1h", "mult": 1.5}},
    {"id": "ema_ribbon", "rx": r"ema[\s-]cross|moving[\s-]average[\s-]cross|golden[\s-]cross|ema[\s-]ribbon",
     "name": "EMA ribbon aligned, near EMA21",
     "conditions": [_c("ema9_1h", ">", "ema21_1h"), _c("ema21_1h", ">", "ema50_1h"), _c("close_4h", ">", "ema50_4h"),
                    _c("dist_ema21_pct_1h", "<=", 1.2), _c("dist_ema21_pct_1h", ">=", 0.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.5}},
    {"id": "volume_breakout", "rx": r"volume[\s-](spike|breakout)|breakout[\s-](strategy|trading|scanner)",
     "name": "Volume-confirmed Donchian breakout",
     "conditions": [_c("vol_rel_1h", ">=", 2.0), _c("close_1h", ">", "donchian_high20_1h"), _c("close_4h", ">", "ema50_4h"),
                    _c("rsi_1h", "<=", 75.0)], "stop": {"type": "atr", "tf": "1h", "mult": 1.6}},
    {"id": "momentum_rotation", "rx": r"momentum|relative[\s-]strength|rotation", "name": "7-day momentum leader",
     "conditions": [_c("ret_168_1h", ">=", 6.0), *UPTREND_4H, _c("rsi_1h", ">=", 50.0), _c("rsi_1h", "<=", 68.0)],
     "stop": {"type": "atr", "tf": "1h", "mult": 1.6}},
]
_BY_ID = {t["id"]: t for t in TEMPLATES}


def match(text: str) -> list[str]:
    """Template ids whose technique is mentioned in the (sanitized) README/description. Pure regex; nothing runs."""
    low = text.lower()
    return [t["id"] for t in TEMPLATES if re.search(t["rx"], low)]


def spec_for(template_id: str, source: str = "") -> RuleSpec:
    t = _BY_ID[template_id]
    desc = f"Our own rule for a technique seen in {source}." if source else "Our own rule template."
    return RuleSpec.model_validate({"name": t["name"], "description": desc[:600], "conditions": t["conditions"],
                                    "stop": t["stop"], "quality": 0.65})
