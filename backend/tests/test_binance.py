import time

import httpx
import pytest

from app.market.binance import (BinanceClient, MarketDataError, StaleDataError, assert_fresh, closed_candles,
                                parse_candles, parse_symbol_info)
from conftest import make_candles

ROW = [1700000000000, "100", "105", "99", "104", "10", 1700003599999, "1040", 5, "0", "0", "0"]


def test_parse_candles_valid_and_invalid():
    cs = parse_candles([ROW, [ROW[0] + 3600000] + ROW[1:6] + [ROW[6] + 3600000] + ROW[7:]])
    assert len(cs) == 2 and cs[0].close == 104
    with pytest.raises(MarketDataError):
        parse_candles([ROW, ROW])  # not increasing
    with pytest.raises(MarketDataError):
        parse_candles([[ROW[0], "100", "98", "99", "104", "10", ROW[6], "1", 1]])  # high < low/close
    with pytest.raises(MarketDataError):
        parse_candles([[ROW[0], "abc"]])
    with pytest.raises(MarketDataError):
        parse_candles({"code": -1})


def test_closed_and_fresh():
    now = int(time.time() * 1000)
    cs = make_candles([1, 2, 3], interval_ms=60_000, start_ms=now - 150_000)
    closed = closed_candles(cs, now)
    assert len(closed) == 2
    assert_fresh(closed, "1m", now)
    with pytest.raises(StaleDataError):
        assert_fresh(make_candles([1, 2], interval_ms=60_000, start_ms=now - 3_600_000), "1m", now)
    with pytest.raises(StaleDataError):
        assert_fresh([], "1h")


def test_parse_symbol_info():
    s = parse_symbol_info({"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING",
                           "isSpotTradingAllowed": True, "permissionSets": [["SPOT", "MARGIN"]],
                           "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                                       {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                                       {"filterType": "NOTIONAL", "minNotional": "5"}]})
    assert s.spot_allowed and s.tick_size == 0.01 and s.min_notional == 5


def _client(handler, **kw):
    return BinanceClient("https://example.test", transport=httpx.MockTransport(handler), max_retries=2, **kw)


async def test_client_retries_server_errors_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] == 1 else httpx.Response(200, json={"symbol": "X", "price": "1.5"})

    import app.market.binance as b
    monkeypatch.setattr(b.asyncio, "sleep", lambda *_: _noop())
    c = _client(h)
    assert await c.price("X") == 1.5
    assert calls["n"] == 2
    assert c.last_ok_at is not None


async def _noop():
    return None


async def test_client_rate_limit_and_geo_block():
    c = _client(lambda r: httpx.Response(429, headers={"retry-after": "30"}))
    with pytest.raises(MarketDataError, match="rate limited"):
        await c.price("X")
    with pytest.raises(MarketDataError, match="backoff"):
        await c.price("X")  # backoff respected, no request sent
    c2 = _client(lambda r: httpx.Response(451, json={"msg": "restricted"}))
    with pytest.raises(MarketDataError, match="451"):
        await c2.ping()


async def test_client_rejects_non_public_endpoints_and_bad_payloads():
    c = _client(lambda r: httpx.Response(200, json={"price": "-1"}))
    with pytest.raises(MarketDataError, match="not allowed"):
        await c._get("/api/v3/order")
    with pytest.raises(MarketDataError, match="non-positive"):
        await c.price("X")
    c2 = _client(lambda r: httpx.Response(200, json={"bidPrice": "2", "askPrice": "1"}))
    with pytest.raises(MarketDataError):
        await c2.book_ticker("X")


async def test_client_never_sends_api_keys():
    seen = {}

    def h(req):
        seen.update(req.headers)
        return httpx.Response(200, json={})

    await _client(h).ping()
    assert not any(k.lower().startswith("x-mbx-apikey") for k in seen)
