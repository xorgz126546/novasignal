import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PriceLevelsChart } from "../components/charts";
import ReportDialog from "../components/ReportDialog";
import { Badge, Bar, ErrorBox, Loading, PageHeader, RiskNote, Section, Stat } from "../components/ui";
import { dt, pct, price, signClass } from "../lib/format";
import { useFetch } from "../lib/useFetch";

const AGENT_NAMES: Record<string, string> = {
  technical_analyst: "02 Technical Analyst", trend_detector: "03 Trend Detector", volume_intelligence: "04 Volume Intelligence",
  support_resistance: "05 Support & Resistance", price_action: "06 Price Action", news_sentiment: "07 News & Sentiment",
  risk_manager: "08 Risk Manager", strategy_validator: "09 Strategy Validator", entry_exit: "10 Entry & Exit Engine",
};

export default function SignalDetail() {
  const { id } = useParams();
  const { data: s, error, loading, reload } = useFetch<any>(`/api/signals/${id}`, 30000);
  const [tf, setTf] = useState("1h");
  const candles = useFetch<any[]>(`/api/signals/${id}/candles?interval=${tf}`);
  const [dlg, setDlg] = useState<"ENTERED" | "SKIPPED" | "CLOSED" | null>(null);
  if (loading && !s) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const ind = s.agent_outputs?.technical_analyst?.indicators?.["1h"];
  const live = ["ACTIVE", "USER_ENTERED", "USER_SKIPPED"].includes(s.status);
  return (
    <div className="space-y-5">
      <Link to="/app/signals" className="text-sm text-zinc-500 hover:text-zinc-200">← Signals</Link>
      <PageHeader title={`${s.pair} · BUY`} subtitle={`Signal #${s.id} · published ${dt(s.created_at)} · Binance Spot`}
        actions={<>
          <Badge>{s.status}</Badge>
          {live && !s.my_decision && <><button className="btn-primary" onClick={() => setDlg("ENTERED")}>I entered</button><button className="btn-ghost" onClick={() => setDlg("SKIPPED")}>Skip</button></>}
          {s.my_trade?.status === "OPEN" && <button className="btn-primary" onClick={() => setDlg("CLOSED")}>Report exit</button>}
        </>} />

      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <Stat label="Entry" value={price(s.entry_price)} />
        <Stat label="Take profit" value={price(s.take_profit)} tone="text-up" sub={pct(s.expected_move_pct)} />
        <Stat label="Stop loss" value={price(s.stop_loss)} tone="text-down" sub={pct(((s.stop_loss - s.entry_price) / s.entry_price) * 100)} />
        <Stat label="Risk / reward" value={`1:${s.risk_reward.toFixed(2)}`} sub={`net 1:${(s.position_sizing?.net_risk_reward ?? 0).toFixed(2)}`} />
        <Stat label="Net if TP hit" value={pct(s.net_expected_return_pct)} sub="after est. fees + slippage" />
        <Stat label="Confidence" value={`${s.confidence.toFixed(0)}/100`} sub="evidence score" />
      </div>

      <Section title="Price & levels" right={
        <div className="flex gap-1">{["15m", "1h", "4h"].map((i) => <button key={i} onClick={() => setTf(i)} className={`px-2.5 py-1 rounded-lg text-xs ${tf === i ? "bg-graphite-600" : "text-zinc-500"}`}>{i}</button>)}</div>}>
        {candles.error ? <ErrorBox error={`Chart unavailable: ${candles.error}`} /> : candles.data ? (
          <PriceLevelsChart candles={candles.data} entry={s.entry_price} tp={s.take_profit} sl={s.stop_loss} createdAt={s.created_at} />
        ) : <div className="h-80 animate-pulse rounded-xl bg-graphite-900" />}
        <p className="text-xs text-zinc-500 mt-2">Live Binance Spot candles (closing prices). Dashed lines: take profit, entry, stop loss.</p>
      </Section>

      <div className="grid lg:grid-cols-3 gap-4">
        <Section title="Why it was approved" className="lg:col-span-2">
          {s.narrative && <p className="text-sm text-zinc-300 mb-4 border-l-2 border-accent pl-3">{s.narrative}</p>}
          <ul className="space-y-2 text-sm text-zinc-300">
            {s.justification.map((j: string, i: number) => <li key={i} className="leading-relaxed">{j}</li>)}
          </ul>
        </Section>
        <Section title="Confidence breakdown">
          <div className="space-y-3">
            {Object.entries(s.confidence_breakdown || {}).map(([k, v]: any) => (
              <div key={k}>
                <div className="flex justify-between text-xs mb-1"><span className="text-zinc-400">{AGENT_NAMES[k] ?? k}</span><span className="num">{v.toFixed(1)}</span></div>
                <Bar value={v} max={20} tone="bg-zinc-300" />
              </div>
            ))}
          </div>
          <p className="text-[11px] text-zinc-500 mt-4">Weighted sum of agent evidence scores (max 100). Not a probability of profit. See docs/CONFIDENCE_MODEL.md.</p>
        </Section>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Section title="Technical indicators (1h)">
          {ind ? (
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              {[["RSI", ind.rsi?.toFixed(1)], ["MACD hist", ind.macd_hist?.toPrecision(4)], ["EMA 9", price(ind.ema9)], ["EMA 21", price(ind.ema21)], ["EMA 50", price(ind.ema50)], ["EMA 200", price(ind.ema200)], ["BB upper", price(ind.bb_upper)], ["BB lower", price(ind.bb_lower)], ["ATR %", ind.atr_pct?.toFixed(2)]].map(([k, v]) => (
                <Fragment key={k}><dt className="text-zinc-500">{k}</dt><dd className="num text-right">{v ?? "—"}</dd></Fragment>
              ))}
            </dl>
          ) : <p className="text-sm text-zinc-500">Not available.</p>}
        </Section>
        <Section title="Position sizing (reference)">
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-zinc-500">Reference equity</dt><dd className="num text-right">{s.position_sizing?.reference_equity_usdt} USDT</dd>
            <dt className="text-zinc-500">Risk per trade</dt><dd className="num text-right">{s.position_sizing?.risk_per_trade_pct}%</dd>
            <dt className="text-zinc-500">Quantity</dt><dd className="num text-right">{s.position_sizing?.quantity?.toPrecision(6)}</dd>
            <dt className="text-zinc-500">Notional</dt><dd className="num text-right">{s.position_sizing?.notional_usdt?.toFixed(2)} USDT</dd>
            <dt className="text-zinc-500">Loss at SL</dt><dd className="num text-right text-down">−{s.position_sizing?.risk_usdt?.toFixed(2)} USDT</dd>
            <dt className="text-zinc-500">Gain at TP</dt><dd className="num text-right text-up">+{s.position_sizing?.reward_usdt?.toFixed(2)} USDT</dd>
          </dl>
          <p className="text-[11px] text-zinc-500 mt-3">Scale to your own capital. Includes estimated fees and slippage.</p>
        </Section>
        <Section title="Tracks for this signal">
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-zinc-500">Theoretical</dt><dd className={`num text-right ${signClass(s.theoretical_return_pct)}`}>{s.theoretical_outcome ? `${pct(s.theoretical_return_pct)}` : "open"}</dd>
            <dt className="text-zinc-500">Paper</dt><dd className={`num text-right ${signClass(s.paper_trade?.return_pct)}`}>{s.paper_trade?.status === "CLOSED" ? pct(s.paper_trade.return_pct) : "open"}</dd>
            <dt className="text-zinc-500">Your trade</dt><dd className={`num text-right ${signClass(s.my_trade?.realized_pct)}`}>{s.my_trade ? (s.my_trade.status === "CLOSED" ? pct(s.my_trade.realized_pct) : s.my_trade.status.toLowerCase()) : "not reported"}</dd>
          </dl>
        </Section>
      </div>

      <Section title="Agent analysis">
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {Object.entries(AGENT_NAMES).map(([k, name]) => {
            const o = s.agent_outputs?.[k];
            if (!o) return null;
            return (
              <div key={k} className="rounded-xl bg-graphite-900 border border-graphite-700 p-3">
                <div className="flex justify-between items-center mb-2"><span className="text-sm font-medium">{name}</span><Badge tone={o.passed ? "ok" : "error"}>{o.passed ? "PASS" : "FAIL"}</Badge></div>
                <ul className="text-xs text-zinc-400 space-y-1">{(o.reasons || []).slice(0, 5).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="Signal history">
        <ol className="space-y-2 text-sm">
          {s.events.map((e: any, i: number) => (
            <li key={i} className="flex gap-3 items-center"><span className="text-xs text-zinc-500 w-36 shrink-0">{dt(e.at)}</span><Badge>{e.to}</Badge><span className="text-zinc-400 truncate">{e.note}</span></li>
          ))}
        </ol>
      </Section>
      <RiskNote />
      {dlg && <ReportDialog signal={s} action={dlg} onClose={() => setDlg(null)} onDone={reload} />}
    </div>
  );
}
