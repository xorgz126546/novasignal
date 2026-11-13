import { useState } from "react";
import { EquityChart, FunnelBars, PnlBars } from "../components/charts";
import { Empty, ErrorBox, Loading, PageHeader, Section, Stat } from "../components/ui";
import { pct, rate, ratio, signClass } from "../lib/format";
import { useFetch } from "../lib/useFetch";

const TRACKS = [
  { k: "paper", label: "Paper", note: "Simulated fills with fees (0.10%/side), slippage and execution delay." },
  { k: "theoretical", label: "Theoretical", note: "Raw signal levels without any costs — optimistic upper bound." },
  { k: "actual", label: "Actual", note: "Only trades you confirmed, with your real prices and fees." },
];

export default function Analytics() {
  const [track, setTrack] = useState("paper");
  const [days, setDays] = useState(30);
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  const { data, error, loading } = useFetch<any>(`/api/analytics?track=${track}&days=${days}`);
  const m = data?.metrics;
  return (
    <div className="space-y-5">
      <PageHeader title="Analytics" subtitle="One track at a time. Theoretical, paper and actual results are never blended into a single number." />
      <div className="flex flex-wrap gap-2 items-center">
        <div className="flex rounded-xl border border-graphite-600 p-1">
          {TRACKS.map((t) => <button key={t.k} onClick={() => setTrack(t.k)} className={`px-3 py-1.5 rounded-lg text-sm ${track === t.k ? "bg-graphite-600 text-white" : "text-zinc-400"}`}>{t.label}</button>)}
        </div>
        <div className="flex rounded-xl border border-graphite-600 p-1">
          {[7, 30, 90, 365].map((d) => <button key={d} onClick={() => setDays(d)} className={`px-3 py-1.5 rounded-lg text-sm ${days === d ? "bg-graphite-600 text-white" : "text-zinc-400"}`}>{d}d</button>)}
        </div>
        <span className="text-xs text-zinc-500">{TRACKS.find((t) => t.k === track)?.note}</span>
      </div>
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} /> : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <Stat label="Win rate" value={rate(m.win_rate)} sub={`${m.wins}W / ${m.losses}L of ${m.trades}`} />
            <Stat label="Profit factor" value={ratio(m.profit_factor)} />
            <Stat label="Expectancy" value={pct(m.expectancy_pct)} tone={signClass(m.expectancy_pct)} sub={m.expectancy_ci95_pct ? `95% CI ${pct(m.expectancy_ci95_pct[0])} … ${pct(m.expectancy_ci95_pct[1])}` : "CI needs ≥2 trades"} />
            <Stat label="Max drawdown" value={pct(m.max_drawdown_pct, 2, false)} sub="compounded, full-size" />
            <Stat label="Avg win / loss" value={<span><span className="text-up">{pct(m.avg_win_pct)}</span> <span className="text-zinc-500">/</span> <span className="text-down">{pct(m.avg_loss_pct)}</span></span>} />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat label="Sum of returns" value={pct(m.sum_return_pct)} tone={signClass(m.sum_return_pct)} />
            <Stat label="Net P/L" value={m.net_pnl != null ? `${m.net_pnl.toFixed(2)} USDT` : "—"} tone={signClass(m.net_pnl)} />
            <Stat label="Fees" value={`${m.total_fees.toFixed(2)} USDT`} />
            <Stat label="Slippage cost" value={`${m.total_slippage_cost.toFixed(2)} USDT`} />
          </div>
          {m.trades === 0 ? (
            <div className="card"><Empty title="No closed trades in this window">Results appear once signals reach their take profit, stop loss or expiry.</Empty></div>
          ) : (
            <div className="grid lg:grid-cols-2 gap-4">
              <Section title="Equity index (start = 100)"><EquityChart data={data.equity_curve} /></Section>
              <Section title="Net return per period" right={
                <div className="flex gap-1">{(["daily", "weekly", "monthly"] as const).map((p) => <button key={p} onClick={() => setPeriod(p)} className={`px-2.5 py-1 rounded-lg text-xs capitalize ${period === p ? "bg-graphite-600" : "text-zinc-500"}`}>{p}</button>)}</div>}>
                <PnlBars data={data[period]} />
              </Section>
            </div>
          )}
          <div className="grid lg:grid-cols-2 gap-4">
            <Section title="Signals generated vs executed">
              <FunnelBars items={[{ label: "Signals generated", value: data.funnel.generated }, { label: "You executed", value: data.funnel.executed }, { label: "You skipped", value: data.funnel.skipped }, { label: "Paper trades still open", value: data.funnel.paper_open }]} />
            </Section>
            <Section title="Table view">
              <div className="overflow-x-auto max-h-64">
                <table className="table">
                  <thead><tr><th>Period</th><th className="text-right">Trades</th><th className="text-right">Wins</th><th className="text-right">Net return</th></tr></thead>
                  <tbody>{data[period].map((r: any) => <tr key={r.period}><td>{r.period}</td><td className="num text-right">{r.trades}</td><td className="num text-right">{r.wins}</td><td className={`num text-right ${signClass(r.sum_return_pct)}`}>{pct(r.sum_return_pct)}</td></tr>)}</tbody>
                </table>
                {data[period].length === 0 && <p className="text-sm text-zinc-500 p-3">No rows.</p>}
              </div>
            </Section>
          </div>
        </>
      )}
    </div>
  );
}
