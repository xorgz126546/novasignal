"""Loop B — Growth: Trend Hunter → Content Editor → drafts → 'Needs you'. Drafts are NEVER posted automatically.

Trend sources (deterministic data): Binance 24h tickers, CoinGecko public 'trending', GitHub repo search.
We do not scrape social networks (paid APIs). Content angles derive from real platform facts.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...market.binance import BinanceClient, MarketDataError, get_client
from ...models import Signal, utcnow
from ...trading_config import load_config
from .. import approvals, bus, guard, llm, runtime
from ..github import GitHub, GitHubError
from ..models import HQDecision, HQDraft, HQReport
from ..registry import can_use
from ..reports import save_report

log = logging.getLogger("cryptonex.hq.growth")
DISCLAIMER_EN = "Not financial advice. No profit is guaranteed. Crypto is risky; you decide and execute your own trades."
DISCLAIMER_AR = "ليست نصيحة مالية ولا يوجد ربح مضمون. العملات الرقمية عالية المخاطر، وأنت من يقرر وينفّذ."
PROMISE = re.compile(r"guarantee|risk[- ]?free|sure (win|profit)|100\s*%\s*(win|profit|accura)|get rich|"
                     r"easy money|never lose|ربح مضمون|أرباح مضمونة|مضمون 100|بدون مخاطر|ثراء سريع", re.I)
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"


def has_promises(text: str) -> bool:
    cleaned = text.replace(DISCLAIMER_EN, "").replace(DISCLAIMER_AR, "")
    cleaned = re.sub(r"no profit is guaranteed|لا يوجد ربح مضمون|ليست نصيحة مالية", "", cleaned, flags=re.I)
    return bool(PROMISE.search(cleaned))


async def coingecko_trending(transport: httpx.AsyncBaseTransport | None = None) -> list[dict]:
    async with httpx.AsyncClient(timeout=10, transport=transport, headers={"User-Agent": "cryptonex-hq"}) as c:
        r = await c.get(COINGECKO_TRENDING)
        r.raise_for_status()
        coins = r.json().get("coins", [])
    return [{"symbol": guard.sanitize(str(x["item"].get("symbol", "")), 20).upper(),
             "name": guard.sanitize(str(x["item"].get("name", "")), 60),
             "rank": x["item"].get("market_cap_rank")} for x in coins[:10] if "item" in x]


async def run_trends(db: Session, client: BinanceClient | None = None, gh: GitHub | None = None,
                     cg_transport: httpx.AsyncBaseTransport | None = None) -> HQReport:
    client = client or get_client()
    risk = load_config(db).risk
    with runtime.task(db, "trend_hunter", "trends", "Collecting crypto narratives and content trends") as t:
        data: dict = {"sources": []}
        try:
            tickers = await client.tickers_24h()
            symbols = await client.exchange_info()
            cfg = load_config(db)
            liquid = [x for x in tickers if x["symbol"].endswith("USDT") and x["symbol"] in symbols
                      and symbols[x["symbol"]].status == "TRADING" and float(x["quoteVolume"]) >= risk.min_quote_volume_usdt / 2
                      and symbols[x["symbol"]].base not in cfg.excluded_bases
                      and not symbols[x["symbol"]].base.endswith(("USD", "USDT", "EUR"))]  # no stablecoins/fiat
            g = sorted(liquid, key=lambda x: float(x["priceChangePercent"]), reverse=True)
            v = sorted(liquid, key=lambda x: float(x["quoteVolume"]), reverse=True)
            data["binance_gainers"] = [{"symbol": x["symbol"], "change_pct": round(float(x["priceChangePercent"]), 2)} for x in g[:5]]
            data["binance_losers"] = [{"symbol": x["symbol"], "change_pct": round(float(x["priceChangePercent"]), 2)} for x in g[-5:]]
            data["binance_volume_leaders"] = [{"symbol": x["symbol"], "quote_volume": round(float(x["quoteVolume"]))} for x in v[:5]]
            data["sources"].append("binance")
        except MarketDataError as e:
            data["binance_error"] = str(e)
        try:
            cg = await coingecko_trending(cg_transport)
            on_binance = {s.removesuffix("USDT") for s in (symbols if "symbols" in locals() else {})}
            for c in cg:
                c["on_binance_usdt"] = c["symbol"] in on_binance
            data["coingecko_trending"] = cg
            data["sources"].append("coingecko")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            data["coingecko_error"] = type(e).__name__
        if can_use(db, "trend_hunter", "github_trending"):
            try:
                since = (utcnow() - timedelta(days=14)).strftime("%Y-%m-%d")
                repos = await (gh or GitHub()).search_repos(f"crypto trading created:>{since}", 5)
                data["github_new_repos"] = [{"repo": guard.sanitize(r["full_name"], 100), "stars": r.get("stargazers_count")} for r in repos]
                data["sources"].append("github")
            except GitHubError as e:
                data["github_error"] = str(e)[:120]
        top = [f"{x['symbol']} {x['change_pct']:+.2f}% (24h)" for x in data.get("binance_gainers", [])[:3]]
        top += [f"Trending on CoinGecko: {c['name']} ({c['symbol']})" + (" — on Binance" if c.get("on_binance_usdt") else "")
                for c in data.get("coingecko_trending", [])[:3]]
        data["top"] = top
        data["content_angles"] = content_angles(db)
        body = "\n".join(["# Trends", "", "## Crypto narratives", *([f"- {x}" for x in top] or ["- no data"]), "",
                          "## Content angles (from our own real data)", *[f"- {a}" for a in data["content_angles"]], "",
                          f"_Sources: {', '.join(data['sources']) or 'none available'}. Trends are not trade signals._"])
        rep = save_report(db, "trends", "Trends", body, data, "trend_hunter")
        t.result = {"summary": f"{len(top)} trends from {', '.join(data['sources']) or 'no source'}"}
    return rep


def content_angles(db: Session) -> list[str]:
    since = utcnow() - timedelta(days=7)
    n_sig = db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= since)) or 0
    n_nt = db.scalar(select(func.count()).select_from(HQDecision).where(HQDecision.symbol == "*", HQDecision.created_at >= since)) or 0
    angles = ["How 11 checks + a risk veto decide BUY or NO TRADE (education)",
              "Why we publish at most 3 signals a day — and often zero"]
    if n_nt:
        angles.append(f"Discipline post: {n_nt} NO TRADE cycle(s) this week and why that protects capital")
    if n_sig:
        angles.append(f"Transparency post: the {n_sig} signal(s) of the week with outcomes, wins and losses")
    return angles


def signal_card_svg(sig: Signal) -> str:
    e = html.escape
    rows = [("Entry", sig.entry_price), ("Take profit", sig.take_profit), ("Stop loss", sig.stop_loss)]
    if sig.invalidation_price:
        rows.append(("Invalidation", sig.invalidation_price))
    items = "".join(f'<text x="80" y="{360 + i * 90}" font-size="38" fill="#a1a1aa">{e(k)}</text>'
                    f'<text x="1000" y="{360 + i * 90}" font-size="44" fill="#fafafa" text-anchor="end" font-family="monospace">{v:,.6g}</text>'
                    for i, (k, v) in enumerate(rows))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080">'
            f'<rect width="1080" height="1080" fill="#050505"/><rect x="40" y="40" width="1000" height="1000" rx="48" fill="#141417" stroke="#26262d" stroke-width="4"/>'
            f'<text x="80" y="140" font-size="44" font-weight="800" fill="#fafafa" letter-spacing="6">CRYPTONEX <tspan fill="#ff2a3d">AI</tspan></text>'
            f'<text x="80" y="250" font-size="72" font-weight="800" fill="#fafafa">{e(sig.base_asset or sig.symbol)}/USDT · BUY</text>'
            f'{items}<text x="80" y="{360 + len(rows) * 90 + 40}" font-size="34" fill="#a1a1aa">R:R 1:{sig.risk_reward:.2f} · Spot only</text>'
            f'<text x="80" y="980" font-size="26" fill="#71717a">{e(DISCLAIMER_EN)}</text></svg>')


async def _draft(db: Session, kind: str, title: str, facts: dict, instruction: str, fallback: str) -> HQDraft | None:
    text, _ = await llm.write_text(db, "content", instruction, facts, f"draft_{kind}", fallback, 900)
    if has_promises(text):
        text = fallback  # LLM text promising profit is discarded
    if has_promises(text):
        return None
    if DISCLAIMER_EN not in text:
        text += f"\n\n{DISCLAIMER_EN}\n{DISCLAIMER_AR}"
    d = HQDraft(kind=kind, lang="both", title=title, body=text, facts=facts)
    db.add(d)
    db.commit()
    a = approvals.request(db, "content", f"Draft: {title}", text[:600], {"draft_id": d.id}, ["content", "trend_hunter"],
                          ref=f"draft:{d.id}")
    d.approval_id = a.id
    db.commit()
    bus.publish(db, "draft", "content", draft_id=d.id, kind=kind, title=title)
    return d


async def run_content(db: Session) -> list[HQDraft]:
    since = utcnow() - timedelta(hours=24)
    sigs = list(db.scalars(select(Signal).where(Signal.created_at >= since).order_by(Signal.id.desc())))
    trend = db.scalar(select(HQReport).where(HQReport.kind == "trends").order_by(HQReport.id.desc()).limit(1))
    nt = db.scalar(select(func.count()).select_from(HQDecision).where(HQDecision.symbol == "*", HQDecision.created_at >= since)) or 0
    facts = {"signals_24h": [{"pair": f"{s.base_asset}/USDT", "entry": s.entry_price, "take_profit": s.take_profit,
                              "stop_loss": s.stop_loss, "status": s.status} for s in sigs[:3]],
             "no_trade_cycles_24h": nt, "trends": (trend.data.get("top", []) if trend else [])[:5]}
    drafts: list[HQDraft] = []
    with runtime.task(db, "content", "drafts", "Writing today's post and reel drafts") as t:
        if sigs:
            s0 = sigs[0]
            fb = (f"📊 {s0.base_asset}/USDT — BUY setup\nEntry {s0.entry_price:,.6g} • TP {s0.take_profit:,.6g} • SL {s0.stop_loss:,.6g}\n"
                  f"إشارة شراء على {s0.base_asset}/USDT: دخول {s0.entry_price:,.6g} • هدف {s0.take_profit:,.6g} • وقف {s0.stop_loss:,.6g}")
        elif nt:
            fb = (f"Today our agents ran {nt} full market check(s) and found NO TRADE. Patience is part of the strategy.\n"
                  f"اليوم أجرى فريقنا {nt} فحص(ات) للسوق والنتيجة: لا صفقة. الصبر جزء من الاستراتيجية.")
        else:
            fb = ("How we decide: every Binance Spot setup must pass structure, strategy and risk checks. "
                  "At most 3 signals a day — often zero.\n"
                  "كيف نقرر: كل فرصة على Binance Spot يجب أن تجتاز فحص الهيكل والاستراتيجية والمخاطر. "
                  "3 إشارات يوميًا كحد أقصى — وغالبًا لا شيء.")
        d = await _draft(db, "post", "Daily post (AR + EN)", facts, "Write a short social post in English then Arabic from "
                         "these facts. Educational, honest, no hype, no promises.", fb)
        drafts += [d] if d else []
        reel_fb = ("REEL (30s): 1) Hook: 'Why our AI said NO TRADE today' 2) Show the market check 3) Explain the risk veto "
                   "4) CTA: follow for transparent results.\nريل (30 ثانية): لماذا قال الذكاء الاصطناعي لا صفقة اليوم؟")
        d = await _draft(db, "reel", "Reel script (AR + EN)", facts, "Write a 30-second reel script (4 numbered beats) in "
                         "English then Arabic from these facts. No promises.", reel_fb)
        drafts += [d] if d else []
        if sigs:
            card = HQDraft(kind="signal_card", lang="en", title=f"Signal card {sigs[0].symbol}", body=signal_card_svg(sigs[0]),
                           facts={"signal_id": sigs[0].id})
            db.add(card)
            db.commit()
            a = approvals.request(db, "content", f"Signal card: {sigs[0].symbol}", "Image card for the latest signal.",
                                  {"draft_id": card.id}, ["content"], ref=f"draft:{card.id}")
            card.approval_id = a.id
            db.commit()
            drafts.append(card)
        t.result = {"summary": f"{len(drafts)} draft(s) waiting for your approval"}
    return drafts


async def run_growth(db: Session) -> None:
    await run_trends(db)
    await run_content(db)
