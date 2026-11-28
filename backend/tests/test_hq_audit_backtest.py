"""Backtester engine + walk-forward gate, Auditor outcomes/NO TRADE audits/health incidents."""
from datetime import timedelta

import pytest

from app.hq.agents import auditor
from app.hq.models import HQAgent, HQDecision, HQIncident, HQXPEvent
from app.hq.strategies.base import Reject, Setup, Strategy
from app.market.backtest import BacktestResult, GateThresholds, Trade, gate, required_t, simulate, walk_forward
from app.market.binance import Candle
from app.models import AgentRun, Signal, utcnow
from app.trading_config import RiskConfig

R = RiskConfig()
H = 3_600_000


def bars(rows, start=1_700_000_000_000, ms=H):
    """rows: (open, high, low, close)"""
    return [Candle(start + i * ms, o, h, l, c, 100, start + (i + 1) * ms - 1, 100 * c, 1) for i, (o, h, l, c) in enumerate(rows)]


class Always(Strategy):
    """Signals on every bar with fixed % levels (test double)."""
    id = "always"

    def __init__(self, tp=1.04, sl=0.987):
        self.tp, self.sl = tp, sl

    def evaluate(self, ctx, p, risk):
        e = ctx.last_price
        return Setup(self.id, ctx.symbol, e, e * self.sl, e * self.tp, e * self.sl, 0.8, [])


class Never(Strategy):
    id = "never"

    def evaluate(self, ctx, p, risk):
        return Reject(self.id, "no")


def flat(n, px=100.0):
    return [(px, px * 1.001, px * 0.999, px) for _ in range(n)]


def test_simulate_next_bar_entry_stop_first_and_fees():
    rows = flat(222) + [(100, 100.1, 99.9, 100)] + [(100, 105, 98, 101)] + flat(60)  # bar touches BOTH levels
    c1 = bars(rows)
    c4 = bars([(100, 100.2, 99.8, 100)] * 80, ms=4 * H)
    res = simulate(Always(), {}, {"X": (c1, c4)}, R, horizon_bars=10)
    first = res.trades[0]
    assert first.entry == pytest.approx(100 * (1 + R.slippage_pct / 100))  # next bar open + slippage
    t2 = [t for t in res.trades if t.outcome == "sl"]
    assert t2, "the bar touching both TP and SL must count as a stop"
    assert all(t.net_pct < 0 for t in t2)
    timeouts = [t for t in res.trades if t.outcome == "timeout"]
    assert timeouts and all(t.net_pct < 0 for t in timeouts)  # flat price: fees+slippage make it negative
    # non-overlapping: each trade starts after the previous one exited
    for a, b in zip(res.trades, res.trades[1:]):
        assert b.entry_time > a.exit_time


def test_simulate_no_lookahead_discards_unfinished_trades():
    c1 = bars(flat(225))
    c4 = bars([(100, 100.2, 99.8, 100)] * 60, ms=4 * H)
    res = simulate(Always(), {}, {"X": (c1, c4)}, R, horizon_bars=50)
    assert res.trades == []  # not enough future bars → discarded, never extrapolated
    assert simulate(Never(), {}, {"X": (c1, c4)}, R).trades == []


def _res(rets):
    return BacktestResult([Trade("X", i * H, i * H + 1, 100, 100, "tp" if r > 0 else "sl", r) for i, r in enumerate(rets)], ["X"], 1000)


def test_gate_min_trades_oos_and_drawdown():
    th = GateThresholds(min_trades=20)
    g = gate(_res([2.0, -1.0] * 5), th)
    assert not g["passed"] and not g["checks"]["min_trades"]
    good = _res([3.0, -1.0, 2.5, -1.0, 3.2, -0.9] * 6)
    assert gate(good, th)["passed"]
    losing_oos = _res([3.0, -1.0] * 14 + [-1.5] * 12)
    assert not gate(losing_oos, th)["checks"]["oos_positive"]


def test_overfitting_penalty_grows_with_variants():
    th = GateThresholds()
    assert required_t(th, 1) == pytest.approx(1.0)
    assert required_t(th, 50) > required_t(th, 5) > required_t(th, 1)
    marginal = _res([1.0, -0.8] * 15)  # weakly positive
    assert gate(marginal, GateThresholds(min_trades=20), variants_tried=1)["required_t_stat"] < \
           gate(marginal, GateThresholds(min_trades=20), variants_tried=200)["required_t_stat"]
    assert not gate(marginal, GateThresholds(min_trades=20), variants_tried=200)["checks"]["overfitting_penalty"]


def test_baseline_comparison_and_folds():
    th = GateThresholds(min_trades=20)
    cand = _res([2.0, -1.0] * 15)
    g = gate(cand, th, baseline={"expectancy_pct": 5.0})
    assert g["checks"]["beats_baseline"] is False and not g["passed"]
    wf = walk_forward(cand, th)
    assert len(wf["folds"]) == 4 and wf["oos"]["trades"] > 0


# ---------------- Auditor ----------------
def mk_hq_signal(db, outcome=None, agent="strat_pullback"):
    now = utcnow()
    s = Signal(symbol="TESTUSDT", base_asset="TEST", entry_price=100, take_profit=104, stop_loss=98.7, expected_move_pct=4,
               risk_reward=3, confidence=70, status="ACTIVE", created_at=now, entry_valid_until=now, expires_at=now,
               source="hq", strategy_id="trend_pullback", agent_outputs={"hq": {"agent_id": agent}},
               theoretical_outcome=outcome)
    db.add(s)
    db.commit()
    return s


def test_outcome_xp_only_for_verified_tp_and_once(db):
    mk_hq_signal(db, "TARGET_HIT")
    mk_hq_signal(db, "STOP_LOSS_HIT")
    mk_hq_signal(db, None)
    auditor.credit_outcomes(db)
    auditor.credit_outcomes(db)  # idempotent
    assert db.get(HQAgent, "strat_pullback").xp == 40 and db.get(HQAgent, "publisher").xp == 40
    assert db.query(HQXPEvent).filter_by(source_type="signal_sl_hit").count() == 1


class FakeClient:
    def __init__(self, candles=None, last_error=None):
        self.c, self.last_error, self.last_ok_at = candles or [], last_error, None

    async def klines_since(self, sym, tf, start, limit=1000):
        return [c for c in self.c if c.open_time >= start]


async def test_no_trade_audit_avoided_loss_awards_risk(db):
    d = HQDecision(cycle_ref="r", symbol="TESTUSDT", decision="NO_TRADE", stage="risk", reasons=["RISK VETO"],
                   strategy_id="trend_pullback", levels={"stop": 98.7, "take_profit": 104},
                   created_at=utcnow() - timedelta(hours=5))
    m = HQDecision(cycle_ref="r", symbol="TESTUSDT", decision="NO_TRADE", stage="publisher", reasons=["low score"],
                   levels={"stop": 90, "take_profit": 101}, created_at=utcnow() - timedelta(hours=5))
    db.add_all([d, m])
    db.commit()
    start = int(d.created_at.timestamp() * 1000) + 1
    cs = bars([(100, 100.5, 99.5, 100), (100, 102, 98.0, 99)], start=start)
    r = await auditor.audit_no_trades(db, FakeClient(cs))
    assert r["avoided_loss"] == 1 and r["missed_win"] == 1
    assert db.get(HQAgent, "risk").xp == 25
    assert (await auditor.audit_no_trades(db, FakeClient(cs)))["avoided_loss"] == 0  # audited once


def test_health_incidents_dedup_and_auto_resolve(db):
    bad = FakeClient(last_error="HTTP 451 restricted")
    assert len(auditor.health_check(db, bad)) == 1
    assert auditor.health_check(db, bad) == []  # deduplicated while open
    auditor.health_check(db, FakeClient())  # healthy again
    assert db.query(HQIncident).filter_by(key="feed:binance").one().status == "resolved"
    for _ in range(5):
        db.add(AgentRun(agent_id="trend_detector", status="error", error="boom", output={}))
    db.commit()
    inc = auditor.health_check(db, FakeClient())
    assert any(i.kind == "repeated_errors" for i in inc)
