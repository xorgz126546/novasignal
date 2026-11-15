"""Growth loop: trends from real-shaped data, drafts never auto-post, no profit promises, approval gate."""
import httpx

from app.hq import approvals, llm
from app.hq.loops import growth
from app.hq.models import HQApproval, HQDraft
from app.market.binance import SymbolInfo
from app.models import Signal, utcnow
from test_hq_upgrade_repair import FakeLLM


class Market:
    async def tickers_24h(self):
        rows = [("AAAUSDT", 12.5), ("BBBUSDT", 8.0), ("CCCUSDT", -9.0), ("USDCUSDT", 0.01), ("RWUSDUSDT", 0.02)]
        return [{"symbol": s, "priceChangePercent": str(c), "quoteVolume": "50000000"} for s, c in rows]

    async def exchange_info(self):
        return {s: SymbolInfo(s, s[:-4], "USDT", "TRADING", True, 0.01, 0.01, 5)
                for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "USDCUSDT", "RWUSDUSDT")}


def cg():
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"coins": [{"item": {"symbol": "aaa", "name": "Aaa Coin",
                                                                                      "market_cap_rank": 50}}]}))


async def test_trends_exclude_stablecoins_and_mark_binance(db):
    rep = await growth.run_trends(db, Market(), cg_transport=cg())
    syms = [x["symbol"] for x in rep.data["binance_gainers"]]
    assert syms[0] == "AAAUSDT" and "USDCUSDT" not in syms and "RWUSDUSDT" not in syms
    assert rep.data["coingecko_trending"][0]["on_binance_usdt"] is True
    assert "not trade signals" in rep.body_md


def test_promise_detector():
    assert growth.has_promises("Guaranteed 100% profit, risk-free!")
    assert growth.has_promises("ربح مضمون كل يوم")
    assert not growth.has_promises("We were patient today. " + growth.DISCLAIMER_EN + " " + growth.DISCLAIMER_AR)


async def test_drafts_need_approval_and_never_promise(db):
    llm.set_client_factory(lambda: FakeLLM("GUARANTEED profits, risk-free, get rich with our signals!"))
    try:
        drafts = await growth.run_content(db)
    finally:
        llm.set_client_factory(None)
    assert drafts and all(not growth.has_promises(d.body) for d in drafts)  # hype text replaced by the honest template
    assert all(growth.DISCLAIMER_EN in d.body for d in drafts)
    assert all(d.status == "draft" for d in drafts)
    pend = db.query(HQApproval).filter_by(kind="content", status="pending").all()
    assert len(pend) == len(drafts)
    approvals.approve(db, pend[0].id, "user:1")
    assert db.get(HQDraft, pend[0].payload["draft_id"]).status == "approved"


async def test_signal_card_svg_is_escaped_and_has_disclaimer(db):
    now = utcnow()
    s = Signal(symbol="X<script>USDT", base_asset="X<script>", entry_price=1.5, take_profit=1.56, stop_loss=1.48,
               expected_move_pct=4, risk_reward=2.5, confidence=70, status="ACTIVE", created_at=now, entry_valid_until=now,
               expires_at=now, invalidation_price=1.49)
    db.add(s)
    db.commit()
    svg = growth.signal_card_svg(s)
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "Not financial advice" in svg and "Invalidation" in svg
