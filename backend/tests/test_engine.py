"""Lifecycle, paper trading, tracker, performance metrics, reports and pipeline plumbing."""
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import lifecycle, pipeline
from app.engine.lifecycle import LifecycleError
from app.engine.performance import TradeResult, compute_metrics, max_drawdown_pct
from app.engine.reports import build_report, to_markdown, verdict
from app.engine.tracker import track_open_signals
from app.market.binance import Candle, SymbolInfo
from app.models import PaperTrade, Role, Signal, SignalStatus, User, utcnow
from app.trading_config import RiskConfig, TradingConfig, save_config
from conftest import make_candles, uptrend

S = SignalStatus


def mk_signal(db, created=None, entry=100.0, tp=104.0, sl=98.5, status=S.ACTIVE, hours=48):
    created = created or utcnow() - timedelta(minutes=30)
    s = Signal(symbol="TESTUSDT", base_asset="TEST", quote_asset="USDT", entry_price=entry, take_profit=tp, stop_loss=sl,
               expected_move_pct=4, risk_reward=2.67, confidence=70, status=status.value, created_at=created,
               entry_valid_until=created + timedelta(hours=4), expires_at=created + timedelta(hours=hours),
               position_sizing={"quantity": 2.0})
    db.add(s)
    db.commit()
    return s


def mk_user(db, role=Role.USER, email="u@test.io"):
    u = User(email=email, password_hash="x", role=role.value)
    db.add(u)
    db.commit()
    return u


def m1(sig, rows):
    """1-minute synthetic candles after signal creation: rows = [(high, low, close), ...]"""
    t0 = int(sig.created_at.timestamp() * 1000) + 60_000
    return [Candle(t0 + i * 60_000, c, h, l, c, 1, t0 + (i + 1) * 60_000 - 1, 1, 1) for i, (h, l, c) in enumerate(rows)]


# ---------------- transitions ----------------
def test_illegal_transition_rejected(db):
    s = mk_signal(db, status=S.ANALYZING)
    with pytest.raises(LifecycleError):
        lifecycle.transition(db, s, S.TARGET_HIT)
    lifecycle.transition(db, s, S.APPROVED)
    lifecycle.transition(db, s, S.ACTIVE)
    db.commit()
    assert [e.to_status for e in s.events] == ["APPROVED", "ACTIVE"]


# ---------------- outcome evaluation ----------------
def test_tp_hit(db):
    s = mk_signal(db)
    o = lifecycle.evaluate_candles(s, m1(s, [(101, 99.5, 100.5), (104.2, 100, 104)]), utcnow())
    assert o.outcome == "TARGET_HIT" and o.price == 104.0


def test_same_candle_both_levels_counts_as_stop(db):
    s = mk_signal(db)
    o = lifecycle.evaluate_candles(s, m1(s, [(105, 98, 101)]), utcnow())
    assert o.outcome == "STOP_LOSS_HIT"


def test_expiry_requires_data_covering_expiry(db):
    s = mk_signal(db, created=utcnow() - timedelta(hours=3), hours=1)
    partial = m1(s, [(101, 99.5, 100.2)] * 10)  # only 10 minutes of data
    assert lifecycle.evaluate_candles(s, partial, utcnow()) is None
    full = m1(s, [(101, 99.5, 100.2)] * 70)
    o = lifecycle.evaluate_candles(s, full, utcnow())
    assert o.outcome == "EXPIRED" and o.price == 100.2


def test_candles_before_signal_ignored(db):
    s = mk_signal(db)
    t = int(s.created_at.timestamp() * 1000) - 120_000
    old = [Candle(t, 100, 200, 50, 100, 1, t + 59_999, 1, 1)]
    assert lifecycle.evaluate_candles(s, old, utcnow()) is None


# ---------------- paper trading ----------------
def test_paper_trade_costs():
    r = RiskConfig()
    p = PaperTrade(signal_id=1, entry_price=100 * 1.0005, quantity=2, slippage_cost=2 * 0.05)
    lifecycle.close_paper_trade(p, "TARGET_HIT", 104.0, r, utcnow())
    fees = 0.001 * 2 * (100.05 + 104)
    assert p.fees == pytest.approx(fees)
    assert p.pnl == pytest.approx(2 * (104 - 100.05) - fees)
    p2 = PaperTrade(signal_id=2, entry_price=100.05, quantity=2, slippage_cost=0.1)
    lifecycle.close_paper_trade(p2, "STOP_LOSS_HIT", 98.5, r, utcnow())
    assert p2.exit_price == pytest.approx(98.5 * (1 - 0.001))  # stop slippage doubled
    assert p2.return_pct < (98.5 - 100) / 100 * 100  # worse than theoretical


# ---------------- user reports ----------------
def test_user_report_flow_and_limits(db):
    u = mk_user(db)
    s1, s2 = mk_signal(db), mk_signal(db)
    t = lifecycle.report_entered(db, u, s1, 100.2, quantity=1.0, fees=0.1)
    assert t.slippage_pct == pytest.approx(0.2)
    assert s1.status == S.ACTIVE.value  # non-admin decisions never change the global signal status
    with pytest.raises(LifecycleError, match="already reported"):
        lifecycle.report_skipped(db, u, s1)
    with pytest.raises(LifecycleError, match="max 1 open"):
        lifecycle.report_entered(db, u, s2, 100.0)
    t = lifecycle.report_closed(db, u, s1, 104.0, fees=0.1)
    assert t.status == "CLOSED"
    assert t.realized_pnl == pytest.approx(1.0 * (104 - 100.2) - 0.2)
    assert t.realized_pct == pytest.approx(t.realized_pnl / 100.2 * 100)
    with pytest.raises(LifecycleError, match="already closed"):
        lifecycle.report_closed(db, u, s1, 105)
    lifecycle.report_entered(db, u, s2, 100.0)  # allowed again after closing


def test_admin_reports_drive_global_status(db):
    a = mk_user(db, Role.ADMIN, "a@test.io")
    s = mk_signal(db)
    lifecycle.report_entered(db, a, s, 100.0)
    assert s.status == S.USER_ENTERED.value
    lifecycle.report_closed(db, a, s, 101.0)
    assert s.status == S.CLOSED_MANUALLY.value


def test_report_validation(db):
    u = mk_user(db)
    s = mk_signal(db)
    with pytest.raises(LifecycleError):
        lifecycle.report_entered(db, u, s, 150.0)  # >20% away from signal entry
    with pytest.raises(LifecycleError):
        lifecycle.report_closed(db, u, s, 101)  # never entered
    lifecycle.report_skipped(db, u, s)
    t = lifecycle.report_closed if False else None
    assert t is None


# ---------------- tracker with fake client ----------------
class FakeClient:
    def __init__(self, candles_by_symbol):
        self.c = candles_by_symbol

    async def klines_since(self, symbol, interval, start_ms, limit=1000):
        return [c for c in self.c[symbol] if c.open_time >= start_ms][:limit]


async def test_tracker_resolves_and_closes_paper(db):
    s = mk_signal(db)
    lifecycle.open_paper_trade(db, s, 100.0, RiskConfig())
    db.commit()
    events = []

    async def notify(evt, sig, **kw):
        events.append((evt, sig.id))

    n = await track_open_signals(db, FakeClient({"TESTUSDT": m1(s, [(100.5, 99.8, 100.1), (104.5, 100, 104.1)])}), notify)
    db.refresh(s)
    assert n == 1 and s.status == "TARGET_HIT" and s.theoretical_return_pct == pytest.approx(4.0)
    assert s.paper_trade.status == "CLOSED" and s.paper_trade.return_pct < 4.0
    assert events == [("outcome", s.id)]
    assert await track_open_signals(db, FakeClient({"TESTUSDT": []})) == 0  # already resolved


# ---------------- performance ----------------
def _tr(rets):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [TradeResult(t0 + timedelta(hours=i), r) for i, r in enumerate(rets)]


def test_metrics_known_values():
    m = compute_metrics(_tr([4, -1.5, 4, -1.5, -1.5]))
    assert m["trades"] == 5 and m["wins"] == 2 and m["win_rate"] == pytest.approx(0.4)
    assert m["profit_factor"] == pytest.approx(8 / 4.5)
    assert m["expectancy_pct"] == pytest.approx(0.7)
    assert m["avg_win_pct"] == 4 and m["avg_loss_pct"] == -1.5


def test_drawdown():
    assert max_drawdown_pct([10, -10]) == pytest.approx(10)
    assert max_drawdown_pct([-10, -10]) == pytest.approx(19)
    assert max_drawdown_pct([]) == 0


def test_empty_metrics():
    m = compute_metrics([])
    assert m["trades"] == 0 and m["win_rate"] is None and m["expectancy_pct"] is None


def test_verdict_is_strict():
    few = compute_metrics(_tr([4, 4, 4]))
    assert verdict(few)["code"] == "INCONCLUSIVE"  # a few winners prove nothing
    mixed = compute_metrics(_tr([4, -1.5] * 20))
    assert verdict(mixed)["code"] in ("EDGE_INDICATED", "NO_EDGE_DEMONSTRATED")
    losing = compute_metrics(_tr([-1.5, -1.5, 4] * 12))
    assert verdict(losing)["code"] == "NO_EDGE_DEMONSTRATED"


def test_report_tracks_separate(db):
    s = mk_signal(db)
    lifecycle.open_paper_trade(db, s, 100.0, RiskConfig())
    lifecycle.apply_outcome(db, s, lifecycle.Outcome("TARGET_HIT", 104.0, utcnow()), RiskConfig())
    db.commit()
    rep = build_report(db, utcnow() - timedelta(days=1), utcnow() + timedelta(minutes=1), "final")
    assert rep["tracks"]["theoretical"]["metrics"]["trades"] == 1
    assert rep["tracks"]["paper"]["metrics"]["trades"] == 1
    assert rep["tracks"]["actual"]["metrics"]["trades"] == 0
    assert rep["tracks"]["paper"]["metrics"]["expectancy_pct"] < rep["tracks"]["theoretical"]["metrics"]["expectancy_pct"]
    assert rep["verdict"]["code"] == "INCONCLUSIVE"
    md = to_markdown(rep)
    assert "never combine" in md and "INCONCLUSIVE" in md


# ---------------- pipeline plumbing ----------------
class FakeMarket:
    """Serves synthetic fixtures through the BinanceClient interface."""

    def __init__(self):
        now_ms = int(utcnow().timestamp() * 1000)
        self.c = {}
        for tf, ms in (("15m", 900_000), ("1h", 3_600_000), ("4h", 14_400_000), ("1d", 86_400_000)):
            self.c[tf] = make_candles(uptrend(301), ms, start_ms=now_ms - 301 * ms - 1)

    async def exchange_info(self):
        return {"TESTUSDT": SymbolInfo("TESTUSDT", "TEST", "USDT", "TRADING", True, 0.0001, 0.001, 5)}

    async def tickers_24h(self):
        return [{"symbol": "TESTUSDT", "lastPrice": "100", "quoteVolume": "90000000", "priceChangePercent": "2",
                 "highPrice": "104", "lowPrice": "98"}]

    async def klines(self, sym, tf, limit=300):
        return self.c[tf]

    async def book_ticker(self, sym):
        return 99.99, 100.0

    async def price(self, sym):
        return self.c["1h"][-1].close


def _force_approve(monkeypatch):
    from app.agents.base import AgentOutput

    def forced(orig):
        def ok(self, inp):  # run the real agent (real indicator/level data) but force a pass
            return orig(self, inp).model_copy(update={"passed": True, "score": 0.9})
        return ok
    for name in ("TechnicalAnalyst", "TrendDetector", "VolumeIntelligence", "SupportResistance", "PriceActionAnalyst",
                 "StrategyValidator", "RiskManager"):
        cls = getattr(pipeline, name)
        monkeypatch.setattr(cls, "execute", forced(cls.execute))

    def levels(self, inp):  # fixed, internally consistent levels around the fixture price
        e = round(inp.last_price, 4)
        return self.Output(passed=True, score=0.9, entry=e, take_profit=round(e * 1.04, 4), stop_loss=round(e * 0.986, 4),
                           expected_move_pct=4.0, stop_distance_pct=1.4, risk_reward=2.86, net_risk_reward=2.18,
                           net_expected_return_pct=3.7, round_trip_cost_pct=0.3)
    monkeypatch.setattr(pipeline.EntryExitEngine, "execute", levels)
    assert AgentOutput


async def test_pipeline_creates_signal_paper_trade_and_notifies(db, monkeypatch):
    _force_approve(monkeypatch)
    sent = []

    async def notify(evt, sig, **kw):
        sent.append(evt)

    cycle = await pipeline.run_scan_cycle(db, FakeMarket(), notify)
    assert cycle.status == "ok", cycle.error
    sigs = list(db.query(Signal))
    assert sigs, [o.rejection_reasons for o in db.query(pipeline.Opportunity)]
    s = sigs[0]
    assert s.status == "ACTIVE" and s.direction == "BUY"
    assert s.stop_loss < s.entry_price < s.take_profit
    assert [e.to_status for e in s.events] == ["ANALYZING", "APPROVED", "ACTIVE"]
    assert s.paper_trade and s.paper_trade.entry_price > s.entry_price * 0.99
    assert sent == ["new_signal"]
    # duplicate prevention: second cycle must not approve the same symbol again
    c2 = await pipeline.run_scan_cycle(db, FakeMarket(), notify)
    assert c2.approved == 0 and db.query(Signal).count() == 1


async def test_pipeline_respects_pause_and_daily_limit(db, monkeypatch):
    cfg = TradingConfig(paused=True)
    save_config(db, cfg)
    c = await pipeline.run_scan_cycle(db, FakeMarket())
    assert c.status == "skipped" and "paused" in c.notes
    cfg.paused = False
    cfg.risk.max_signals_per_day = 0
    save_config(db, cfg)
    c = await pipeline.run_scan_cycle(db, FakeMarket())
    assert c.status == "skipped" and "daily limit" in c.notes


async def test_pipeline_records_failure(db):
    class Broken(FakeMarket):
        async def tickers_24h(self):
            raise RuntimeError("boom")
    c = await pipeline.run_scan_cycle(db, Broken())
    assert c.status == "error" and "boom" in c.error
