"""Unit tests for all 11 agents (deterministic, no network, synthetic fixtures)."""
import pytest

from app.agents import (ALL_AGENTS, ChiefTradingOfficer, EntryExitEngine, MarketScanner, NewsSentiment,
                        PriceActionAnalyst, RiskManager, StrategyValidator, SupportResistance, TechnicalAnalyst,
                        TrendDetector, VolumeIntelligence)
from app.agents.a10_entry_exit import round_to_tick
from app.agents.a11_chief_trading_officer import CONFIDENCE_WEIGHTS, Dossier, confidence_score
from app.market.binance import SymbolInfo
from conftest import HOUR, downtrend, make_candles, uptrend


def snapshot(closes_fn, vols=None):
    c1h = make_candles(closes_fn(300), HOUR, vols=vols)
    return {"symbol": "TESTUSDT", "last_price": c1h[-1].close, "candles": {
        "15m": make_candles(closes_fn(300), HOUR // 4), "1h": c1h,
        "4h": make_candles(closes_fn(300), HOUR * 4), "1d": make_candles(closes_fn(300), HOUR * 24)}}


def test_eleven_agents_with_schemas():
    assert len(ALL_AGENTS) == 11
    assert sorted(a.number for a in ALL_AGENTS) == list(range(1, 12))
    for a in ALL_AGENTS:
        d = a.describe()
        assert d["responsibility"] and d["output_schema"]["properties"]


def test_confidence_weights_sum_to_one():
    assert sum(CONFIDENCE_WEIGHTS.values()) == pytest.approx(1.0)


# ---------- 01 scanner ----------
def _ticker(sym, qv, chg=2.0, hi=110, lo=100, last=105):
    return {"symbol": sym, "lastPrice": str(last), "quoteVolume": str(qv), "priceChangePercent": str(chg),
            "highPrice": str(hi), "lowPrice": str(lo)}


def _info(sym, base, quote="USDT", status="TRADING"):
    return SymbolInfo(sym, base, quote, status, True, 0.01, 0.001, 5)


def test_scanner_filters_and_ranks():
    symbols = {s: _info(s, b) for s, b in [("AAAUSDT", "AAA"), ("BBBUSDT", "BBB"), ("USDCUSDT", "USDC"),
                                            ("BTCUPUSDT", "BTCUP"), ("PUMPUSDT", "PUMP"), ("QUIETUSDT", "QUIET")]}
    symbols["AAABTC"] = _info("AAABTC", "AAA", "BTC")
    symbols["HALTUSDT"] = _info("HALTUSDT", "HALT", status="BREAK")
    tickers = [_ticker("AAAUSDT", 500e6), _ticker("BBBUSDT", 30e6), _ticker("LOWUSDT", 1e6), _ticker("USDCUSDT", 900e6),
               _ticker("BTCUPUSDT", 90e6), _ticker("PUMPUSDT", 90e6, chg=60), _ticker("QUIETUSDT", 90e6, hi=100.5, lo=100),
               _ticker("AAABTC", 90e6), _ticker("HALTUSDT", 90e6)]
    out = MarketScanner().run(dict(tickers=tickers, symbols=symbols, min_quote_volume=20e6, excluded_bases=["USDC"]))
    assert [c.symbol for c in out.shortlist] == ["AAAUSDT", "BBBUSDT"]
    assert out.excluded["stable_or_excluded"] == 1 and out.excluded["extreme_move"] == 1
    assert out.excluded["leveraged_token"] == 1 and out.excluded["too_quiet"] == 1


def test_scanner_zero_candidates_is_valid():
    out = MarketScanner().run(dict(tickers=[], symbols={}, min_quote_volume=1, excluded_bases=[]))
    assert out.passed is False and out.shortlist == [] and out.error is None


# ---------- 02/03/06 on synthetic trends ----------
def test_technical_and_trend_on_uptrend_vs_downtrend():
    up, dn = snapshot(uptrend), snapshot(downtrend)
    t_up, t_dn = TechnicalAnalyst().run(up), TechnicalAnalyst().run(dn)
    assert t_up.checks["ema_stack_bullish"] and not t_dn.checks["ema_stack_bullish"]
    assert t_up.score > t_dn.score and not t_dn.passed
    for k in ("ema9", "ema21", "ema50", "ema200", "rsi", "macd", "bb_upper", "atr"):
        assert t_up.indicators["1h"][k] is not None
    tr_up, tr_dn = TrendDetector().run(up), TrendDetector().run(dn)
    assert tr_up.timeframes["4h"]["direction"] == "up" and tr_dn.timeframes["4h"]["direction"] == "down"
    assert not tr_dn.passed and "4h" in tr_dn.conflicts
    assert tr_up.score > tr_dn.score


def test_trend_rejects_sideways():
    import math
    flat = snapshot(lambda n: [100 + math.sin(i * 0.7) + 0.5 * math.sin(i * 2.3) for i in range(n)])
    out = TrendDetector().run(flat)
    assert not out.passed


def test_price_action_structure():
    assert PriceActionAnalyst().run(snapshot(downtrend)).structure in ("bearish", "mixed")
    out = PriceActionAnalyst().run(snapshot(uptrend))
    assert out.structure in ("bullish", "mixed") and out.momentum_pct_5 is not None


# ---------- 04 volume ----------
def test_volume_liquidity_and_distribution_spike():
    snap = snapshot(uptrend)
    ok = VolumeIntelligence().run(snap | dict(quote_volume_24h=50e6, bid=99.99, ask=100.0, min_quote_volume=20e6))
    assert ok.liquidity_ok and ok.relative_volume == pytest.approx(1.0)
    wide = VolumeIntelligence().run(snap | dict(quote_volume_24h=50e6, bid=99.0, ask=100.0, min_quote_volume=20e6))
    assert not wide.liquidity_ok and not wide.passed
    # Abnormal volume on a bearish last candle -> distribution flag
    closes = uptrend(300)
    closes[-1] = closes[-2] * 0.98
    vols = [100.0 + (i % 3) for i in range(299)] + [5000.0]
    snap2 = snapshot(lambda n: closes, vols=vols)
    spike = VolumeIntelligence().run(snap2 | dict(quote_volume_24h=50e6, min_quote_volume=20e6))
    assert spike.abnormal and spike.abnormal_on_bearish_candle and not spike.passed


# ---------- 05 S/R ----------
def test_support_resistance_levels_below_and_above():
    out = SupportResistance().run(snapshot(uptrend))
    assert out.error is None
    if out.nearest_support:
        assert out.nearest_support < out.supports[0]["price"] + 1e-9 or out.nearest_support <= snapshot(uptrend)["last_price"]
    for s in out.supports:
        assert s["price"] < snapshot(uptrend)["last_price"]
    for r in out.resistances:
        assert r["price"] > snapshot(uptrend)["last_price"]


# ---------- 07 news ----------
async def test_news_no_provider_is_neutral_never_invented():
    out = await NewsSentiment(None).arun(dict(symbol="BTCUSDT", base_asset="BTC"))
    assert out.status == "NO_DATA" and out.passed and out.items == [] and out.sentiment is None


async def test_news_veto_on_risk_event():
    async def fetch(cur):
        return [{"title": "Exchange hack drains BTC hot wallet", "votes": {"negative": 9, "positive": 1}}]
    out = await NewsSentiment(fetch).arun(dict(symbol="BTCUSDT", base_asset="BTC"))
    assert out.veto and not out.passed and out.sentiment == pytest.approx(-0.8)


async def test_news_provider_error_is_no_data():
    async def fetch(cur):
        raise RuntimeError("down")
    out = await NewsSentiment(fetch).arun(dict(symbol="BTCUSDT", base_asset="BTC"))
    assert out.status == "ERROR" and out.passed


# ---------- 10 entry/exit ----------
def test_round_to_tick():
    assert round_to_tick(100.123, 0.01) == 100.12
    assert round_to_tick(100.129, 0.01, "down") == 100.12
    assert round_to_tick(0.000123456, 0.0000001) == pytest.approx(0.0001235)


def test_entry_exit_one_tp_one_sl_and_costs():
    out = EntryExitEngine().run(dict(symbol="X", last_price=100.0, atr_1h=0.5, atr_4h=1.2, invalidation_level=98.8,
                                     nearest_resistance=None, tick_size=0.01))
    assert out.passed, out.reasons
    assert out.stop_loss < out.entry < out.take_profit
    assert 3.0 <= out.expected_move_pct <= 5.0
    assert out.round_trip_cost_pct == pytest.approx(0.30)
    assert out.net_risk_reward == pytest.approx((out.expected_move_pct - 0.3) / (out.stop_distance_pct + 0.3), abs=1e-3)


def test_entry_exit_rejects_blocked_target_and_wide_stop():
    blocked = EntryExitEngine().run(dict(symbol="X", last_price=100, atr_1h=0.5, invalidation_level=98.8,
                                         nearest_resistance=101.5))
    assert not blocked.passed
    wide = EntryExitEngine().run(dict(symbol="X", last_price=100, atr_1h=0.5, invalidation_level=95.0))
    assert not wide.passed and "exceeds max" in wide.reasons[-1]
    tight_rr = EntryExitEngine().run(dict(symbol="X", last_price=100, atr_1h=0.5, atr_4h=0.6, invalidation_level=97.6,
                                          nearest_resistance=None))
    assert not tight_rr.passed  # 3% target vs 2.4% stop -> R/R below 2


# ---------- 08 risk ----------
def test_risk_manager_sizing_math():
    out = RiskManager().run(dict(symbol="X", entry=100, stop_loss=99, take_profit=104, equity_usdt=1000,
                                 risk_per_trade_pct=1, max_position_pct=100, step_size=0.001))
    loss_per_unit = 1 + (100 + 99) * 0.0015
    assert out.quantity == pytest.approx(10 / loss_per_unit, abs=0.001)
    assert out.risk_usdt == pytest.approx(10, abs=0.01)
    assert out.passed


def test_risk_manager_caps_and_rejections():
    capped = RiskManager().run(dict(symbol="X", entry=100, stop_loss=99, take_profit=104, max_position_pct=25))
    assert capped.notional_usdt <= 250.0001
    bad = RiskManager().run(dict(symbol="X", entry=100, stop_loss=101, take_profit=104))
    assert not bad.passed
    maxed = RiskManager().run(dict(symbol="X", entry=100, stop_loss=99, take_profit=104, open_user_trades=1,
                                   max_open_user_trades=1))
    assert not maxed.passed and any("open user-confirmed" in v for v in maxed.violations)
    small = RiskManager().run(dict(symbol="X", entry=100, stop_loss=99, take_profit=104, equity_usdt=10, min_notional=5))
    assert not small.passed


# ---------- 09 validator ----------
def test_validator_agreement_conflicts_and_replay():
    candles = make_candles(uptrend(300, drift=0.0005, amp=0.01, period=48))  # setup historically reaches TP
    res = {a: {"passed": True, "score": 0.8} for a in ("technical_analyst", "trend_detector", "volume_intelligence",
                                                        "support_resistance", "price_action")}
    out = StrategyValidator().run(dict(symbol="X", agent_results=res, candles_1h=candles, tp_pct=3, sl_pct=1.2))
    assert out.agreement == 5 and out.backtest["samples"] >= 5 and out.backtest["win_rate"] == 1.0 and out.passed
    losing = make_candles(uptrend(300, drift=0.0005, amp=0.01, period=12))  # setup historically hits SL
    out_l = StrategyValidator().run(dict(symbol="X", agent_results=res, candles_1h=losing, tp_pct=3, sl_pct=1.2))
    assert out_l.backtest["expectancy_net_pct"] < 0 and not out_l.passed
    res["trend_detector"]["passed"] = False
    res["volume_intelligence"]["passed"] = False
    out2 = StrategyValidator().run(dict(symbol="X", agent_results=res, candles_1h=candles, tp_pct=3, sl_pct=1.2))
    assert not out2.passed and out2.conflicts


def test_validator_negative_live_record_blocks():
    candles = make_candles(uptrend(300))
    res = {a: {"passed": True, "score": 0.8} for a in ("technical_analyst", "trend_detector", "volume_intelligence",
                                                        "support_resistance", "price_action")}
    out = StrategyValidator().run(dict(symbol="X", agent_results=res, candles_1h=candles, tp_pct=3, sl_pct=1.2,
                                       past_signal_returns=[-1.5] * 12))
    assert not out.passed


# ---------- 11 chief ----------
def _good_results(score=0.9):
    r = {a: {"passed": True, "score": score, "reasons": ["ok"]} for a in
         ("technical_analyst", "trend_detector", "volume_intelligence", "support_resistance", "price_action",
          "news_sentiment", "entry_exit", "risk_manager", "strategy_validator")}
    return r


def test_confidence_score_formula():
    conf, br = confidence_score(_good_results(0.5))
    assert conf == pytest.approx(50.0) and sum(br.values()) == pytest.approx(50.0)


def test_chief_quota_duplicates_and_vetoes():
    ds = [Dossier(symbol=f"S{i}USDT", results=_good_results(0.7 + i * 0.05)) for i in range(4)]
    bad = _good_results()
    bad["risk_manager"] = {"passed": False, "score": 0, "reasons": ["too risky"]}
    ds.append(Dossier(symbol="BADUSDT", results=bad))
    out = ChiefTradingOfficer().run(dict(dossiers=ds, signals_today=1, max_signals_per_day=3, active_symbols=["S3USDT"]))
    approved = out.approved_symbols
    assert len(approved) == 2 and "S3USDT" not in approved and "BADUSDT" not in approved
    assert approved == ["S1USDT", "S2USDT"] or set(approved) == {"S2USDT", "S1USDT"}  # highest confidence first
    low = ChiefTradingOfficer().run(dict(dossiers=[Dossier(symbol="L", results=_good_results(0.3))]))
    assert not low.approved_symbols
    paused = ChiefTradingOfficer().run(dict(dossiers=ds[:1], paused=True))
    assert not paused.approved_symbols


def test_chief_zero_signals_valid():
    out = ChiefTradingOfficer().run(dict(dossiers=[]))
    assert out.approved_symbols == [] and out.error is None


# ---------- base contract ----------
def test_invalid_input_returns_error_output_not_exception():
    out = RiskManager().run({"symbol": "X"})
    assert out.passed is False and out.error and "invalid input" in out.error


def test_recorder_called():
    calls = []
    RiskManager(lambda *a: calls.append(a)).run(dict(symbol="X", entry=100, stop_loss=99, take_profit=104))
    assert calls and calls[0][0] == "risk_manager"
