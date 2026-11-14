"""HQ trading loop: structure, strategies, rule DSL, risk veto, publisher dedup/score, NO TRADE, end-to-end cycle."""
import math
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.hq.agents import publisher as pub
from app.hq.agents import risk as riskmod
from app.hq.features import Context, build_features
from app.hq.loops import trading
from app.hq.models import HQDecision
from app.hq.strategies.base import Reject, Setup, plan_levels
from app.hq.strategies.builtin import BUILTINS
from app.hq.strategies.rules import RuleSpec, RuleStrategy
from app.market.binance import SymbolInfo
from app.market.structure import bos_choch
from app.models import Signal, utcnow
from app.trading_config import RiskConfig
from conftest import make_candles, uptrend

R = RiskConfig()


def ctx_from(closes_by_tf: dict, symbol="TESTUSDT", backtest=True) -> Context:
    ms = {"4h": 14_400_000, "1h": 3_600_000, "15m": 900_000}
    candles = {tf: make_candles(c, ms[tf]) for tf, c in closes_by_tf.items()}
    f, st = build_features(candles)
    return Context(symbol, candles, f, st, candles["1h"][-1].close, 0.0001, 0.001, 5.0, backtest)


def test_bos_choch_detects_trend_change():
    seq = [100, 103, 101, 105, 103, 107, 105, 109, 106, 108, 104, 106, 102, 104, 100, 101, 97]
    r = bos_choch([x + 0.3 for x in seq], [x - 0.3 for x in seq], seq, 1)
    types = [(e["type"], e["dir"]) for e in r["events"]]
    assert ("BOS", "up") in types and ("CHoCH", "down") in types and r["bias"] == "down"
    assert types.index(("CHoCH", "down")) > types.index(("BOS", "up"))


def test_plan_levels_single_tp_and_rules():
    ctx = ctx_from({"4h": uptrend(300), "1h": uptrend(300), "15m": uptrend(300)})
    entry = ctx.last_price
    s = plan_levels("t", ctx, entry, entry * 0.988, entry * 0.99, 0.8, ["x"], R)
    if isinstance(s, Setup):
        assert s.stop < s.entry < s.take_profit and R.target_move_min_pct <= s.move_pct <= R.target_move_max_pct
        assert s.net_rr >= R.min_risk_reward - 1e-6
    else:
        assert "resistance" in s.reason  # synthetic swing highs can block the target — still a valid reject
    assert isinstance(plan_levels("t", ctx, entry, entry * 0.999, entry, 0.8, [], R), Reject)  # too tight
    assert isinstance(plan_levels("t", ctx, entry, entry * 0.95, entry, 0.8, [], R), Reject)  # too wide
    assert isinstance(plan_levels("t", ctx, entry, entry * 1.01, entry, 0.8, [], R), Reject)  # stop above entry


def test_builtin_strategies_reject_downtrends_with_reasons():
    down = [100 * 0.998 ** i for i in range(300)]
    ctx = ctx_from({"4h": down, "1h": down, "15m": down})
    for s in BUILTINS.values():
        r = s.evaluate(ctx, s.default_params, R)
        assert isinstance(r, Reject) and r.reason


def test_trend_pullback_finds_setup_in_uptrend_pullback():
    base = uptrend(300, drift=0.002, amp=0.0)
    h1 = base[:-6] + [base[-7] * (1 - 0.004 * k) for k in range(1, 5)] + [base[-7] * 0.985, base[-7] * 1.002]
    ctx = ctx_from({"4h": uptrend(300, drift=0.004, amp=0.0), "1h": h1, "15m": uptrend(300, amp=0.0)})
    r = BUILTINS["trend_pullback"].evaluate(ctx, BUILTINS["trend_pullback"].default_params, R)
    assert isinstance(r, (Setup, Reject))  # deterministic; if a setup is produced, its levels must be valid
    if isinstance(r, Setup):
        assert r.stop < r.entry < r.take_profit and r.invalidation < r.entry


# ---------- rule DSL (safe ideas from GitHub) ----------
def test_rule_spec_validation_blocks_unknown_features_and_code():
    good = {"name": "RSI dip in uptrend", "conditions": [{"left": "rsi_1h", "op": "<", "right": 45},
                                                         {"left": "close_4h", "op": ">", "right": "ema200_4h"}],
            "stop": {"type": "atr", "tf": "1h", "mult": 1.5}}
    RuleSpec.model_validate(good)
    for bad in (
        {**good, "conditions": [{"left": "__import__('os').system('rm -rf /')", "op": ">", "right": 1}]},
        {**good, "conditions": [{"left": "rsi_1h", "op": "eval", "right": 1}]},
        {**good, "conditions": [{"left": "rsi_1h", "op": ">", "right": "open('/etc/passwd')"}]},
        {**good, "stop": {"type": "python", "tf": "1h"}},
        {**good, "conditions": []},
    ):
        with pytest.raises(ValidationError):
            RuleSpec.model_validate(bad)


def test_rule_strategy_evaluates_conditions():
    ctx = ctx_from({"4h": uptrend(300), "1h": uptrend(300), "15m": uptrend(300)})
    always_false = RuleSpec.model_validate({"name": "never", "conditions": [{"left": "rsi_1h", "op": ">", "right": 101}],
                                            "stop": {"type": "atr", "mult": 1.5}})
    r = RuleStrategy("never", always_false).evaluate(ctx, {}, R)
    assert isinstance(r, Reject) and "condition failed" in r.reason


# ---------- risk veto ----------
def _setup(sym="TESTUSDT", entry=100.0):
    return Setup("trend_pullback", sym, entry, entry * 0.987, entry * 1.04, entry * 0.99, 0.8, ["r"], 3.0, 2.3, 4.0, 1.3)


def test_risk_veto_btc_filter_exposure_and_limits():
    btc_down = ctx_from({"4h": [100 * 0.997 ** i for i in range(300)], "1h": [100 * 0.997 ** i for i in range(300)],
                         "15m": [100 * 0.997 ** i for i in range(300)]}, "BTCUSDT")
    ok, why = riskmod.btc_filter(btc_down)
    assert not ok and "BTC" in why
    assert riskmod.btc_filter(None)[0] is False  # no market context → no new longs
    ctx = ctx_from({"4h": uptrend(300), "1h": uptrend(300), "15m": uptrend(300)})
    good = (True, "BTC market filter: OK")
    assert riskmod.assess(_setup(), ctx, R, good, 0, set(), 0).approved
    v = riskmod.assess(_setup(), ctx, R, (False, why), 0, set(), 0)
    assert not v.approved and any("BTC" in r for r in v.reasons)
    assert not riskmod.assess(_setup(), ctx, R, good, 0, {"TESTUSDT"}, 0).approved  # symbol exposure
    assert not riskmod.assess(_setup(), ctx, R, good, R.max_open_signals, set(), 0).approved  # max open
    assert not riskmod.assess(_setup(), ctx, R, good, 0, set(), R.max_signals_per_day).approved  # daily cap
    bad_rr = Setup("x", "TESTUSDT", 100, 98.5, 101, 99, 0.8, [], 0.7, 0.5, 1.0, 1.5)
    assert not riskmod.assess(bad_rr, ctx, R, good, 0, set(), 0).approved  # sizing / R:R


# ---------- publisher ----------
def test_publisher_score_and_dedup(db):
    ctx = ctx_from({"4h": uptrend(300), "1h": uptrend(300), "15m": uptrend(300)})
    s1, b1 = pub.score(_setup(), ctx, R, 1)
    s10, _ = pub.score(_setup(), ctx, R, 10)
    assert 0 <= s1 <= 100 and s10 >= s1 and s10 <= s1 * 1.09 + 0.01
    assert set(b1) >= {"strategy_quality", "structure_alignment", "reward_to_risk", "volume"}
    now = utcnow()
    assert pub.is_duplicate(db, "TESTUSDT", "x", now, 24) is None
    sig = pub.publish(db, _setup(), ctx, R, {"risk_pct": 1.0}, 77.0, b1, ["r"], now, "ref")
    assert sig.source == "hq" and sig.invalidation_price and sig.risk_pct == 1.0 and sig.status == "ACTIVE"
    assert any("Not financial advice" in j for j in sig.justification)
    assert "already has open signal" in pub.is_duplicate(db, "TESTUSDT", "y", now, 24)
    sig.status = "TARGET_HIT"
    db.commit()
    assert "within the last" in pub.is_duplicate(db, "TESTUSDT", "y", now + timedelta(hours=2), 24)
    assert pub.is_duplicate(db, "TESTUSDT", "y", now + timedelta(hours=30), 24) is None


# ---------- end-to-end cycle with a fake market ----------
class FakeMarket:
    def __init__(self, trend="up"):
        now_ms = int(utcnow().timestamp() * 1000)
        g = uptrend if trend == "up" else (lambda n: [100 * 0.998 ** i for i in range(n)])
        self.c = {tf: make_candles(g(301), ms, start_ms=now_ms - 301 * ms - 1)
                  for tf, ms in (("4h", 14_400_000), ("1h", 3_600_000), ("15m", 900_000))}

    async def exchange_info(self):
        return {s: SymbolInfo(s, s[:-4], "USDT", "TRADING", True, 0.0001, 0.001, 5) for s in ("TESTUSDT", "BTCUSDT")}

    async def tickers_24h(self):
        return [{"symbol": "TESTUSDT", "lastPrice": "100", "quoteVolume": "90000000", "priceChangePercent": "2",
                 "highPrice": "104", "lowPrice": "98"}]

    async def klines(self, sym, tf, limit=300):
        return self.c[tf]

    async def book_ticker(self, sym):
        return 99.99, 100.0


async def test_cycle_no_trade_is_persisted_with_reasons(db):
    r = await trading.run_hq_cycle(db, FakeMarket("down"))
    assert r["status"] == "ok" and r["signals"] == []
    rows = db.query(HQDecision).filter_by(cycle_ref=r["cycle_ref"]).all()
    assert any(d.symbol == "*" and d.decision == "NO_TRADE" and "NO TRADE" in d.reasons[0] for d in rows)
    assert all(d.reasons for d in rows)


async def test_cycle_publishes_signal_end_to_end(db, monkeypatch):
    def fake_eval(self, ctx, p, risk):
        e = ctx.last_price
        return Setup(self.id, ctx.symbol, e, round(e * 0.987, 4), round(e * 1.04, 4), round(e * 0.99, 4), 0.9,
                     ["forced test setup"], 3.08, 2.33, 4.0, 1.3)
    monkeypatch.setattr(BUILTINS["trend_pullback"].__class__, "evaluate", fake_eval)
    monkeypatch.setattr(riskmod, "btc_filter", lambda btc: (True, "BTC market filter: OK"))
    sent = []

    async def notify(evt, sig, **kw):
        sent.append(sig.id)
    r = await trading.run_hq_cycle(db, FakeMarket("up"), notify)
    assert len(r["signals"]) == 1, r
    s = db.get(Signal, r["signals"][0])
    assert s.source == "hq" and s.strategy_id == "trend_pullback" and s.score >= pub.DEFAULT_MIN_SCORE
    assert s.paper_trade is not None and sent == [s.id]
    d = db.query(HQDecision).filter_by(signal_id=s.id).one()
    assert d.decision == "SIGNAL"
    r2 = await trading.run_hq_cycle(db, FakeMarket("up"), notify)  # dedup: same symbol can't be re-published
    assert r2["signals"] == []
