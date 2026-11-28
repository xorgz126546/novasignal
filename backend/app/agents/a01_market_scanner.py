"""AGENT 01 — MARKET SCANNER: liquidity/volume/volatility filter over all Binance Spot USDT pairs."""
from __future__ import annotations

import math

from pydantic import BaseModel

from ..market.binance import SymbolInfo
from .base import AgentInput, AgentOutput, BaseAgent

LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


class ScannerInput(AgentInput):
    tickers: list[dict]
    symbols: dict[str, SymbolInfo]
    quote_asset: str = "USDT"
    min_quote_volume: float
    excluded_bases: list[str]
    shortlist_size: int = 12
    max_change_pct: float = 25.0  # skip parabolic pumps (chasing risk)
    min_change_pct: float = -15.0  # skip collapsing markets


class Candidate(BaseModel):
    symbol: str
    base: str
    last_price: float
    quote_volume: float
    change_pct: float
    range_pct: float
    score: float


class ScannerOutput(AgentOutput):
    shortlist: list[Candidate] = []
    scanned: int = 0
    excluded: dict[str, int] = {}


class MarketScanner(BaseAgent[ScannerInput, ScannerOutput]):
    id = "market_scanner"
    number = 1
    name = "Market Scanner"
    responsibility = "Scan Binance Spot pairs, filter by liquidity/volume/volatility, output a ranked shortlist."
    Input = ScannerInput
    Output = ScannerOutput

    def execute(self, inp: ScannerInput) -> ScannerOutput:
        excluded: dict[str, int] = {}

        def skip(reason: str):
            excluded[reason] = excluded.get(reason, 0) + 1

        cands: list[Candidate] = []
        for t in inp.tickers:
            sym = t.get("symbol", "")
            info = inp.symbols.get(sym)
            if info is None or info.quote != inp.quote_asset:
                skip("not_quote_pair")
                continue
            if info.status != "TRADING" or not info.spot_allowed:
                skip("not_spot_trading")
                continue
            if info.base in inp.excluded_bases:
                skip("stable_or_excluded")
                continue
            if info.base.endswith(LEVERAGED_SUFFIXES) and len(info.base) > 4:
                skip("leveraged_token")
                continue
            try:
                price = float(t["lastPrice"])
                qv = float(t["quoteVolume"])
                chg = float(t["priceChangePercent"])
                hi, lo = float(t["highPrice"]), float(t["lowPrice"])
            except (KeyError, TypeError, ValueError):
                skip("bad_ticker")
                continue
            if price <= 0 or lo <= 0:
                skip("bad_ticker")
                continue
            if qv < inp.min_quote_volume:
                skip("low_liquidity")
                continue
            if not (inp.min_change_pct <= chg <= inp.max_change_pct):
                skip("extreme_move")
                continue
            rng = (hi - lo) / lo * 100
            if rng < 1.5:
                skip("too_quiet")  # can't plausibly move 3–5%
                continue
            # Ranking: liquidity (log scale) + volatility in a tradable band + positive momentum.
            liq = min(math.log10(qv / inp.min_quote_volume + 1) / 2, 1.0)
            vol = 1.0 - min(abs(rng - 8.0) / 8.0, 1.0)
            mom = max(min(chg / 10.0, 1.0), -1.0)
            score = 0.45 * liq + 0.35 * vol + 0.20 * (mom + 1) / 2
            cands.append(Candidate(symbol=sym, base=info.base, last_price=price, quote_volume=qv, change_pct=chg,
                                   range_pct=round(rng, 3), score=round(score, 4)))
        cands.sort(key=lambda c: c.score, reverse=True)
        shortlist = cands[: inp.shortlist_size]
        return ScannerOutput(
            passed=bool(shortlist), score=1.0 if shortlist else 0.0, shortlist=shortlist, scanned=len(inp.tickers),
            excluded=excluded,
            reasons=[f"{len(shortlist)} candidates from {len(inp.tickers)} tickers"]
            if shortlist else ["no market met liquidity/volatility filters"],
        )
