"""AGENT 07 — NEWS AND SENTIMENT.

Uses a real news provider ONLY if configured (CryptoPanic API key). Without a provider it reports
status=NO_DATA and stays neutral: it never invents news or sentiment. It can VETO a signal when
reliable data shows invalidating events (hack, delisting, exploit, regulatory action...).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from .base import AgentInput, AgentOutput, BaseAgent

RISK_KEYWORDS = ("hack", "exploit", "delist", "delisting", "lawsuit", "sec charges", "suspend", "insolvency",
                 "rug", "drained", "halt", "investigation", "bankrupt", "unlock")

NewsFetcher = Callable[[str], Awaitable[list[dict]]]


class NewsInput(AgentInput):
    symbol: str
    base_asset: str


class NewsOutput(AgentOutput):
    status: str = "NO_DATA"  # OK | NO_DATA | ERROR
    items: list[dict] = []
    risk_events: list[str] = []
    sentiment: float | None = None  # -1..1, only when computed from real votes
    veto: bool = False


def cryptopanic_fetcher(api_key: str, base_url: str = "https://cryptopanic.com/api/developer/v2/posts/") -> NewsFetcher:
    async def fetch(currency: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(base_url, params={"auth_token": api_key, "currencies": currency, "public": "true"})
            r.raise_for_status()
            return r.json().get("results", [])
    return fetch


class NewsSentiment(BaseAgent[NewsInput, NewsOutput]):
    id = "news_sentiment"
    number = 7
    name = "News & Sentiment"
    responsibility = "Monitor real crypto news for the asset, flag invalidating events, compute vote-based sentiment when available."
    Input = NewsInput
    Output = NewsOutput

    def __init__(self, fetcher: NewsFetcher | None = None, recorder=None):
        super().__init__(recorder)
        self.fetcher = fetcher

    async def execute(self, inp: NewsInput) -> NewsOutput:  # type: ignore[override]
        if self.fetcher is None:
            return NewsOutput(passed=True, score=0.5, status="NO_DATA",
                              reasons=["no news provider configured — neutral, no sentiment claimed"])
        try:
            posts = await self.fetcher(inp.base_asset)
        except Exception as e:
            return NewsOutput(passed=True, score=0.5, status="ERROR", error=None,
                              reasons=[f"news provider error ({type(e).__name__}); treated as no data"])
        items, risks, pos, neg = [], [], 0, 0
        for p in posts[:20]:
            title = str(p.get("title", ""))
            items.append({"title": title, "published_at": p.get("published_at"), "url": p.get("url") or p.get("original_url")})
            low = title.lower()
            hits = [k for k in RISK_KEYWORDS if k in low]
            if hits:
                risks.append(f"{title} [{', '.join(hits)}]")
            votes = p.get("votes") or {}
            pos += int(votes.get("positive", 0) or 0) + int(votes.get("liked", 0) or 0)
            neg += int(votes.get("negative", 0) or 0) + int(votes.get("disliked", 0) or 0) + int(votes.get("toxic", 0) or 0)
        sentiment = (pos - neg) / (pos + neg) if pos + neg >= 5 else None
        veto = bool(risks)
        reasons = [f"{len(items)} recent headlines"]
        if risks:
            reasons.append("risk events: " + "; ".join(risks[:3]))
        reasons.append(f"vote sentiment {sentiment:+.2f}" if sentiment is not None else "too few votes for sentiment")
        score = 0.5 if sentiment is None else (sentiment + 1) / 2
        return NewsOutput(passed=not veto, score=round(0.0 if veto else score, 4), status="OK", items=items,
                          risk_events=risks, sentiment=sentiment, veto=veto, reasons=reasons)
