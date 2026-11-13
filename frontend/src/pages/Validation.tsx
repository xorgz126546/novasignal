import { useState } from "react";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Section, Stat } from "../components/ui";
import { api, download } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dt, pct, rate, ratio } from "../lib/format";
import { useFetch } from "../lib/useFetch";

const ROWS: [string, string, (v: any) => string][] = [
  ["Closed trades", "trades", (v) => String(v ?? 0)],
  ["Win rate", "win_rate", rate],
  ["Expectancy / trade", "expectancy_pct", (v) => pct(v)],
  ["Profit factor", "profit_factor", ratio],
  ["Avg win", "avg_win_pct", (v) => pct(v)],
  ["Avg loss", "avg_loss_pct", (v) => pct(v)],
  ["Max drawdown", "max_drawdown_pct", (v) => pct(v, 2, false)],
  ["Fees (USDT)", "total_fees", (v) => (v ?? 0).toFixed(2)],
  ["Slippage (USDT)", "total_slippage_cost", (v) => (v ?? 0).toFixed(2)],
];

export default function Validation() {
  const { user } = useAuth();
  const progs = useFetch<any[]>("/api/validation");
  const [kind, setKind] = useState<"daily" | "weekly" | "final">("daily");
  const prog = progs.data?.[0];
  const rep = useFetch<any>(prog ? `/api/validation/${prog.id}/report?kind=${kind}` : null);
  const [err, setErr] = useState<string | null>(null);

  const start = async () => {
    setErr(null);
    try {
      await api("/api/validation", { method: "POST", json: { days: 30 } });
      progs.reload();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  if (progs.loading && !progs.data) return <Loading />;
  if (progs.error) return <ErrorBox error={progs.error} />;
  return (
    <div className="space-y-5">
      <PageHeader title="30-Day Validation" subtitle="Determines whether the system shows a measurable edge after realistic costs. A few winning trades are not evidence; the verdict requires ≥30 closed paper trades and a 95% confidence interval above zero."
        actions={user?.role === "admin" && !progs.data?.some((p) => p.status === "running") ? <button className="btn-primary" onClick={start}>Start 30-day program</button> : null} />
      {err && <ErrorBox error={err} />}
      {!prog ? (
        <div className="card"><Empty title="No validation program yet">An admin starts the program once Binance data and Telegram are verified (see docs/VALIDATION_PLAN.md).</Empty></div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat label="Program" value={<span className="text-base">{prog.name}</span>} sub={<Badge>{prog.status}</Badge>} />
            <Stat label="Day" value={`${prog.day} / ${prog.days_total}`} />
            <Stat label="Started" value={<span className="text-base">{dt(prog.started_at)}</span>} />
            <Stat label="Ends" value={<span className="text-base">{dt(prog.ends_at)}</span>} />
          </div>
          <div className="flex gap-2 flex-wrap items-center">
            <div className="flex rounded-xl border border-graphite-600 p-1">
              {(["daily", "weekly", "final"] as const).map((k) => <button key={k} onClick={() => setKind(k)} className={`px-3 py-1.5 rounded-lg text-sm capitalize ${kind === k ? "bg-graphite-600 text-white" : "text-zinc-400"}`}>{k}</button>)}
            </div>
            <button className="btn-ghost" onClick={() => download(`/api/validation/${prog.id}/report?kind=${kind}&format=md`, `cryptonex_${kind}_report.md`)}>Download .md</button>
          </div>
          {rep.loading && !rep.data ? <Loading /> : rep.error ? <ErrorBox error={rep.error} /> : rep.data && (
            <>
              {rep.data.verdict && (
                <div className={`card card-pad border-l-4 ${rep.data.verdict.code === "EDGE_INDICATED" ? "border-l-up" : rep.data.verdict.code === "INCONCLUSIVE" ? "border-l-amber-400" : "border-l-down"}`}>
                  <div className="label">Verdict</div>
                  <div className="text-lg font-bold mt-1">{rep.data.verdict.code.replace(/_/g, " ")}</div>
                  <p className="text-sm text-zinc-400 mt-1">{rep.data.verdict.text}</p>
                </div>
              )}
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
                <Stat label="Opportunities" value={rep.data.opportunities.evaluated} sub={`${rep.data.opportunities.rejected} rejected`} />
                <Stat label="Signals" value={rep.data.signals.generated} />
                <Stat label="Executed" value={rep.data.signals.executed_by_user} />
                <Stat label="Skipped" value={rep.data.signals.skipped_by_user} />
                <Stat label="Avg entry slippage" value={pct(rep.data.signals.avg_entry_slippage_pct, 3)} />
              </div>
              <Section title="Performance by track (never combined)">
                <div className="overflow-x-auto -mx-4 sm:mx-0">
                  <table className="table">
                    <thead><tr><th>Metric</th><th className="text-right">Theoretical</th><th className="text-right">Paper</th><th className="text-right">Actual</th></tr></thead>
                    <tbody>{ROWS.map(([label, key, f]) => (
                      <tr key={key}><td className="text-zinc-400">{label}</td>{["theoretical", "paper", "actual"].map((t) => <td key={t} className="num text-right">{f(rep.data.tracks[t].metrics[key])}</td>)}</tr>
                    ))}</tbody>
                  </table>
                </div>
              </Section>
              <Section title="Top rejection reasons">
                {rep.data.opportunities.top_rejection_reasons.length === 0 ? <p className="text-sm text-zinc-500">None in this window.</p> : (
                  <ul className="space-y-1 text-sm">{rep.data.opportunities.top_rejection_reasons.map(([r, c]: any) => <li key={r} className="flex justify-between"><span className="text-zinc-400">{r}</span><span className="num">{c}</span></li>)}</ul>
                )}
              </Section>
              <p className="text-xs text-zinc-500">{rep.data.disclaimer}</p>
            </>
          )}
        </>
      )}
    </div>
  );
}
