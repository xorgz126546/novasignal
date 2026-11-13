"""Binance Spot PUBLIC market-data client.

Security: this client never sends API keys and only calls public GET endpoints. It cannot
place orders or withdraw funds. Defaults to https://data-api.binance.vision (Binance's public
market-data-only host).

Data integrity: every response is validated; malformed, stale or missing data raises
MarketDataError. Nothing is ever fabricated or back-filled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger("cryptonex.binance")

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
_ALLOWED_PATHS = {"/api/v3/ping", "/api/v3/time", "/api/v3/exchangeInfo", "/api/v3/ticker/24hr",
                  "/api/v3/klines", "/api/v3/ticker/price", "/api/v3/ticker/bookTicker"}
# Request weights (Binance docs); used by the local weight budget.
_WEIGHTS = {"/api/v3/exchangeInfo": 20, "/api/v3/ticker/24hr": 80, "/api/v3/klines": 2, "/api/v3/ticker/price": 4,
            "/api/v3/ticker/bookTicker": 4}


class MarketDataError(Exception):
    pass


class StaleDataError(MarketDataError):
    pass


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: int  # ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int  # ms
    quote_volume: float
    trades: int


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    base: str
    quote: str
    status: str
    spot_allowed: bool
    tick_size: float
    step_size: float
    min_notional: float


def parse_candles(raw: list) -> list[Candle]:
    if not isinstance(raw, list):
        raise MarketDataError("klines response is not a list")
    out: list[Candle] = []
    prev_open = -1
    for row in raw:
        try:
            c = Candle(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]),
                       int(row[6]), float(row[7]), int(row[8]))
        except (IndexError, TypeError, ValueError) as e:
            raise MarketDataError(f"malformed kline row: {e}") from e
        if c.open_time <= prev_open:
            raise MarketDataError("klines not strictly increasing in time")
        if min(c.open, c.high, c.low, c.close) <= 0 or c.high < c.low or c.volume < 0:
            raise MarketDataError(f"invalid OHLCV values at {c.open_time}")
        if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high):
            raise MarketDataError(f"OHLC inconsistency at {c.open_time}")
        prev_open = c.open_time
        out.append(c)
    return out


def closed_candles(candles: list[Candle], now_ms: int | None = None) -> list[Candle]:
    """Drop the still-forming last candle: analysis uses only closed candles."""
    now_ms = now_ms or int(time.time() * 1000)
    return [c for c in candles if c.close_time < now_ms]


def assert_fresh(candles: list[Candle], interval: str, now_ms: int | None = None, max_lag_intervals: float = 2.0) -> None:
    if not candles:
        raise StaleDataError("no candles")
    now_ms = now_ms or int(time.time() * 1000)
    lag = now_ms - candles[-1].close_time
    if lag > INTERVAL_SECONDS[interval] * 1000 * max_lag_intervals:
        raise StaleDataError(f"last {interval} candle is {lag / 1000:.0f}s old")


def parse_symbol_info(s: dict) -> SymbolInfo:
    filters = {f["filterType"]: f for f in s.get("filters", [])}
    perms = set(s.get("permissions") or [])
    for ps in s.get("permissionSets") or []:
        perms.update(ps)
    return SymbolInfo(
        symbol=s["symbol"], base=s["baseAsset"], quote=s["quoteAsset"], status=s["status"],
        spot_allowed=bool(s.get("isSpotTradingAllowed")) and ("SPOT" in perms or not perms),
        tick_size=float(filters.get("PRICE_FILTER", {}).get("tickSize", 0) or 0),
        step_size=float(filters.get("LOT_SIZE", {}).get("stepSize", 0) or 0),
        min_notional=float((filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}).get("minNotional", 0) or 0),
    )


class WeightBudget:
    """Client-side request-weight budget (per rolling minute) on top of server header feedback."""

    def __init__(self, max_per_minute: int):
        self.max = max_per_minute
        self.events: list[tuple[float, int]] = []
        self.server_used: int = 0
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.events = [(t, w) for t, w in self.events if now - t < 60]
                used = sum(w for _, w in self.events)
                if used + weight <= self.max and self.server_used + weight <= self.max * 1.5:
                    self.events.append((now, weight))
                    return
                wait = 60 - (now - self.events[0][0]) if self.events else 1.0
                log.warning("Binance weight budget reached (%s); waiting %.1fs", used, wait)
                await asyncio.sleep(max(wait, 0.5))
                self.server_used = 0


class BinanceClient:
    def __init__(self, base_url: str, timeout: float = 10.0, max_weight_per_minute: int = 1000,
                 transport: httpx.AsyncBaseTransport | None = None, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport,
                                         headers={"User-Agent": "cryptonex-ai/1.0"})
        self.budget = WeightBudget(max_weight_per_minute)
        self.max_retries = max_retries
        self.banned_until: float = 0.0
        self.last_ok_at: datetime | None = None
        self.last_error: str | None = None
        self._symbols: dict[str, SymbolInfo] = {}
        self._symbols_at: float = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        if path not in _ALLOWED_PATHS:  # defence in depth: only public read-only endpoints
            raise MarketDataError(f"endpoint not allowed: {path}")
        if time.monotonic() < self.banned_until:
            raise MarketDataError("Binance rate-limit backoff in effect")
        weight = _WEIGHTS.get(path, 1)
        if path == "/api/v3/ticker/24hr" and params and "symbol" in params:
            weight = 2
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            await self.budget.acquire(weight)
            try:
                r = await self._client.get(path, params=params)
            except httpx.HTTPError as e:
                self.last_error = f"network: {e!r}"
                if attempt == self.max_retries:
                    raise MarketDataError(self.last_error) from e
                await asyncio.sleep(delay)
                delay *= 2
                continue
            used = r.headers.get("x-mbx-used-weight-1m")
            if used and used.isdigit():
                self.budget.server_used = int(used)
            if r.status_code in (418, 429):
                retry_after = float(r.headers.get("retry-after", "60"))
                self.banned_until = time.monotonic() + retry_after
                self.last_error = f"rate limited ({r.status_code}); retry after {retry_after}s"
                raise MarketDataError(self.last_error)
            if r.status_code == 451:
                self.last_error = "Binance refused this location (451). Use BINANCE_BASE_URL=https://data-api.binance.vision or deploy in an eligible region."
                raise MarketDataError(self.last_error)
            if r.status_code >= 500:
                self.last_error = f"server error {r.status_code}"
                if attempt == self.max_retries:
                    raise MarketDataError(self.last_error)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                raise MarketDataError(self.last_error)
            try:
                data = r.json()
            except ValueError as e:
                raise MarketDataError("invalid JSON") from e
            self.last_ok_at = datetime.now(timezone.utc)
            self.last_error = None
            return data
        raise MarketDataError("unreachable")

    async def ping(self) -> bool:
        await self._get("/api/v3/ping")
        return True

    async def exchange_info(self, force: bool = False) -> dict[str, SymbolInfo]:
        if self._symbols and not force and time.monotonic() - self._symbols_at < 3600:
            return self._symbols
        data = await self._get("/api/v3/exchangeInfo", {"permissions": "SPOT"})
        syms = data.get("symbols") if isinstance(data, dict) else None
        if not syms:
            raise MarketDataError("exchangeInfo returned no symbols")
        self._symbols = {s["symbol"]: parse_symbol_info(s) for s in syms}
        self._symbols_at = time.monotonic()
        return self._symbols

    async def tickers_24h(self) -> list[dict]:
        data = await self._get("/api/v3/ticker/24hr")
        if not isinstance(data, list) or not data:
            raise MarketDataError("24hr ticker returned no data")
        return data

    async def klines(self, symbol: str, interval: str, limit: int = 300) -> list[Candle]:
        if interval not in INTERVAL_SECONDS:
            raise MarketDataError(f"unsupported interval {interval}")
        raw = await self._get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)})
        return parse_candles(raw)

    async def klines_since(self, symbol: str, interval: str, start_ms: int, limit: int = 1000) -> list[Candle]:
        raw = await self._get("/api/v3/klines", {"symbol": symbol, "interval": interval, "startTime": start_ms,
                                                  "limit": min(limit, 1000)})
        return parse_candles(raw)

    async def price(self, symbol: str) -> float:
        data = await self._get("/api/v3/ticker/price", {"symbol": symbol})
        try:
            p = float(data["price"])
        except (KeyError, TypeError, ValueError) as e:
            raise MarketDataError("invalid price payload") from e
        if p <= 0:
            raise MarketDataError("non-positive price")
        return p

    async def book_ticker(self, symbol: str) -> tuple[float, float]:
        data = await self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        try:
            bid, ask = float(data["bidPrice"]), float(data["askPrice"])
        except (KeyError, TypeError, ValueError) as e:
            raise MarketDataError("invalid bookTicker payload") from e
        if bid <= 0 or ask <= 0 or ask < bid:
            raise MarketDataError("invalid bid/ask")
        return bid, ask


_client: BinanceClient | None = None


def get_client() -> BinanceClient:
    global _client
    if _client is None:
        from ..config import get_settings

        s = get_settings()
        _client = BinanceClient(s.binance_base_url, s.binance_timeout_s, s.binance_max_weight_per_minute)
    return _client


def set_client(client: BinanceClient | None) -> None:
    global _client
    _client = client
