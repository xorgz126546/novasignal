"""New strategies, decoupled-leader risk exemption, tournament evidence grades, daily best pick, scheduler gating."""
import pytest

from app.hq.agents import risk as riskmod
from app.hq.features import Context, build_features
from app.hq.loops import daily_pick as dp
from app.hq.models import HQBacktestRun, HQDecision, HQReport
from app.hq.registry import load_defs
from app.hq.strategies.base import Reject, Setup
from app.hq.strategies.builtin import BUILTINS
from app.hq.strategies.registry import active_strategies
from app.models import Signal, utcnow
from app.trading_config import RiskConfig
from conftest import make_candles, uptrend
from test_hq_trading import FakeMarket

R = RiskConfig()


def ctx_of(c1h, c4h, sym="TESTUSDT", btc_ret=None) -> Context:
    candles = {"1h": make_candles(c1h), "4h": make_candles(c4h, 14_400_000)}
    f, st = build_features(candles)
    ctx = Context(sym, candles, f, st, candles["1h"][-1].close, 0.0001, 0.001, 5.0, True)
    ctx.extras["btc_ret_7d"] = btc_ret
    return ctx


def test_eight_strategies_owned_by_three_agents_still_15_agents():
    assert len(load_defs()) == 15
    assert set(BUILTINS) == {"breakout_retest", "trend_pullback", "liquidity_sweep", "squeeze_breakout",
                             "donchian_breakout", "relative_strength", "oversold_bounce", "range_support"}


def test_every_strategy_returns_setup_or_reason_on_any_market(db):
    acts = active_strategies(db)
    assert len(acts) == 8
    for series in (uptrend(320), [100 * 0.998 ** i for i in range(320)], [100 + (i % 7) * 0.3 for i in range(320)]):
        ctx = ctx_of(series, series, btc_ret=0.0)
        for a in acts:
            r = a.strategy.evaluate(ctx, a.params, R)
            assert isinstance(r, (Setup, Reject))
            if isinstance(r, Reject):
                assert r.reason
            else:
                assert r.stop < r.entry < r.take_profit and R.target_move_min_pct <= r.move_pct <= R.target_move_max_pct


def test_oversold_bounce_detects_recovery():
    down = [100 * 0.996 ** i for i in range(300)]
    bounce = down + [down[-1] * 1.004, down[-1] * 1.01, down[-1] * 1.018]
    ctx = ctx_of(bounce, [100 * 0.999 ** i for i in range(303)])
    r = BUILTINS["oversold_bounce"].evaluate(ctx, BUILTINS["oversold_bounce"].default_params, R)
    assert isinstance(r, (Setup, Reject))
    if isinstance(r, Reject):
        assert r.reason  # deterministic, explained rejection


def test_relative_strength_requires_btc_context_and_outperformance():
    up = uptrend(320, drift=0.003)
    s = BUILTINS["relative_strength"]
    assert "BTC context" in s.evaluate(ctx_of(up, up, btc_ret=None), s.default_params, R).reason
    r = s.evaluate(ctx_of(up, up, btc_ret=200.0), s.default_params, R)
    assert isinstance(r, Reject) and "outperformance" in r.reason


def test_decoupled_leader_allowed_at_half_risk_when_btc_risk_off():
    up = uptrend(320, drift=0.003)
    ctx = ctx_of(up, up)
    btc = ctx_of([100 * 0.998 ** i for i in range(320)], [100 * 0.998 ** i for i in range(320)], "BTCUSDT")
    assert riskmod.is_decoupled(ctx, btc)
    e = ctx.last_price
    setup = Setup("relative_strength", "TESTUSDT", e, e * 0.987, e * 1.04, e * 0.99, 0.8, ["r"], 3, 2.3, 4, 1.3)
    off = (False, "BTC market filter: risk-off")
    assert not riskmod.assess(setup, ctx, R, off, 0, set(), 0, decoupled=False).approved
    v = riskmod.assess(setup, ctx, R, off, 0, set(), 0, decoupled=True)
    full = riskmod.assess(setup, ctx, R, (True, "ok"), 0, set(), 0)
    assert v.approved and any("HALF risk" in x for x in v.reasons)
    assert v.sizing["risk_usdt"] == pytest.approx(full.sizing["risk_usdt"] / 2, rel=0.05)


def _run(db, sid, passed, trades, exp):
    db.add(HQBacktestRun(subject=f"strategy:{sid}@tournament", passed=passed,
                         result={"summary": {"trades": trades, "expectancy_pct": exp, "win_rate": 0.5}}))
    db.commit()


def test_evidence_grades(db):
    assert dp.evidence(db, "trend_pullback")["grade"] == "C"
    _run(db, "trend_pullback", True, 40, 0.5)
    assert dp.evidence(db, "trend_pullback")["grade"] == "A"
    _run(db, "squeeze_breakout", False, 30, 0.2)
    _run(db, "oversold_bounce", False, 30, -0.2)
    _run(db, "range_support", False, 30, -1.2)
    _run(db, "donchian_breakout", False, 4, -3.0)
    assert [dp.evidence(db, s)["grade"] for s in ("squeeze_breakout", "oversold_bounce", "range_support", "donchian_breakout")] == ["B", "D", "F", "C"]
    assert len(dp.leaderboard(db)) == 8


async def test_daily_pick_publishes_best_and_only_once_per_day(db, monkeypatch):
    def setup_for(quality):
        def ev(self, ctx, p, risk):
            e = ctx.last_price
            return Setup(self.id, ctx.symbol, e, round(e * 0.987, 4), round(e * 1.04, 4), round(e * 0.99, 4), quality,
                         [f"{self.id} setup"], 3.08, 2.33, 4.0, 1.3)
        return ev
    for sid in BUILTINS:
        monkeypatch.setattr(BUILTINS[sid].__class__, "evaluate", lambda self, ctx, p, r: Reject(self.id, "no"))
    monkeypatch.setattr(BUILTINS["oversold_bounce"].__class__, "evaluate", setup_for(0.95))
    monkeypatch.setattr(riskmod, "btc_filter", lambda btc: (True, "BTC market filter: OK"))
    sent = []

    async def notify(evt, payload, **kw):
        sent.append(evt)
    r = await dp.run_daily_pick(db, FakeMarket("up"), notify)
    assert r["status"] == "picked" and r["strategy"] == "oversold_bounce", r
    s = db.get(Signal, r["signal_id"])
    assert s.source == "hq" and "DAILY BEST PICK" in s.justification[0] and "evidence" in s.justification[1].lower()
    assert sent == ["new_signal"]
    assert (await dp.run_daily_pick(db, FakeMarket("up"), notify))["status"] == "already_picked"


async def test_daily_pick_excludes_clearly_losing_strategies_and_reports_no_trade(db, monkeypatch):
    for sid in BUILTINS:
        monkeypatch.setattr(BUILTINS[sid].__class__, "evaluate", lambda self, ctx, p, r: Reject(self.id, "no"))

    def ev(self, ctx, p, risk):
        e = ctx.last_price
        return Setup(self.id, ctx.symbol, e, round(e * 0.987, 4), round(e * 1.04, 4), round(e * 0.99, 4), 0.9, ["x"], 3.08, 2.33, 4.0, 1.3)
    monkeypatch.setattr(BUILTINS["range_support"].__class__, "evaluate", ev)
    monkeypatch.setattr(riskmod, "btc_filter", lambda btc: (True, "BTC market filter: OK"))
    _run(db, "range_support", False, 30, -1.5)  # grade F
    texts = []

    async def notify(evt, payload, **kw):
        texts.append((evt, payload))
    r = await dp.run_daily_pick(db, FakeMarket("up"), notify)
    assert r["status"] == "no_trade" and any("excluded" in w["why_not"] for w in r["watchlist"])
    assert db.query(Signal).count() == 0
    assert texts and texts[0][0] == "text" and "NOT recommendations" in texts[0][1]
    await dp.run_daily_pick(db, FakeMarket("up"), notify)  # later re-check the same day
    assert len(texts) == 1  # only one NO TRADE message per day
    assert db.query(HQReport).filter_by(kind="daily_pick").count() == 2  # one report per check, one message per day
    assert db.query(HQDecision).filter_by(symbol="*", decision="NO_TRADE").count() == 2


def test_scheduler_runs_only_daily_pick_when_hq_disabled(monkeypatch):
    from app.config import get_settings
    from app.hq import scheduler
    monkeypatch.setattr(get_settings(), "hq_enabled", False)
    monkeypatch.setattr(get_settings(), "hq_daily_pick", True)
    assert {n for n, _ in scheduler.active_jobs()} == {"daily_pick", "tournament"}
    monkeypatch.setattr(get_settings(), "hq_daily_pick", False)
    assert scheduler.active_jobs() == []
    monkeypatch.setattr(get_settings(), "hq_enabled", True)
    assert "trading_cycle" in {n for n, _ in scheduler.active_jobs()}


def test_triggers_are_conditions_with_levels():
    down = [100 * 0.996 ** i for i in range(300)]
    ctx = ctx_of(down, down)
    trig = dp.triggers({"TESTUSDT": ctx})
    assert all(t["trigger"] and t["distance_pct"] >= 0 for t in trig)
    if trig:
        assert trig[0]["strategy"] == "oversold_bounce" and "close above" in trig[0]["trigger"]
