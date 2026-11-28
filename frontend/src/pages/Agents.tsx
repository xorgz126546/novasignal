import { useState } from "react";
import { Badge, ErrorBox, Loading, PageHeader, Section } from "../components/ui";
import { ago, dt } from "../lib/format";
import { useFetch } from "../lib/useFetch";

export default function Agents() {
  const { data, error, loading } = useFetch<any[]>("/api/agents", 30000);
  const [sel, setSel] = useState<string | null>(null);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const runs = useFetch<any[]>(sel ? `/api/agents/${sel}/runs?limit=40&errors_only=${errorsOnly}` : null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="space-y-5">
      <PageHeader title="Agent Monitor" subtitle="Eleven independent agents with typed inputs/outputs. Numbers are computed deterministically; nothing here is model-generated." />
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
        {data!.map((a) => {
          const total = (a.approved ?? 0) + (a.rejected ?? 0);
          const c = a.contribution || {};
          return (
            <button key={a.id} onClick={() => setSel(a.id)} className={`card card-pad text-left transition hover:border-graphite-400 ${sel === a.id ? "border-accent/60" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="num text-xs text-zinc-500">AGENT {String(a.number).padStart(2, "0")}</div>
                  <div className="font-semibold mt-0.5">{a.name}</div>
                </div>
                <Badge>{a.status}</Badge>
              </div>
              <p className="text-xs text-zinc-400 mt-2 line-clamp-2">{a.responsibility}</p>
              <div className="grid grid-cols-4 gap-2 mt-4 text-center">
                <div><div className="label">Runs</div><div className="num mt-1">{a.runs}</div></div>
                <div><div className="label">Pass</div><div className="num mt-1">{total ? `${Math.round(((a.approved ?? 0) / total) * 100)}%` : "—"}</div></div>
                <div><div className="label">Errors 24h</div><div className={`num mt-1 ${a.errors_24h ? "text-down" : ""}`}>{a.errors_24h}</div></div>
                <div><div className="label">Avg ms</div><div className="num mt-1">{a.avg_ms ? a.avg_ms.toFixed(0) : "—"}</div></div>
              </div>
              <div className="text-[11px] text-zinc-500 mt-3 flex justify-between">
                <span>Last run {ago(a.last_run)}</span>
                <span title="Average score on signals that later hit TP vs SL (theoretical track)">
                  {c.resolved_signals ? `score W ${c.avg_score_winners?.toFixed(2) ?? "—"} / L ${c.avg_score_losers?.toFixed(2) ?? "—"}` : "no resolved signals yet"}
                </span>
              </div>
            </button>
          );
        })}
      </div>
      {sel && (
        <Section title={`Recent executions — ${data!.find((a) => a.id === sel)?.name}`}
          right={<label className="text-xs text-zinc-400 flex items-center gap-2"><input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> errors only</label>}>
          {runs.loading ? <Loading /> : runs.error ? <ErrorBox error={runs.error} /> : (
            <div className="overflow-x-auto -mx-4 sm:mx-0">
              <table className="table">
                <thead><tr><th>Time</th><th>Symbol</th><th>Result</th><th className="text-right">Score</th><th className="text-right">ms</th><th>Reasons / error</th></tr></thead>
                <tbody>
                  {runs.data!.map((r) => (
                    <tr key={r.id}>
                      <td className="text-zinc-400">{dt(r.at)}</td>
                      <td>{r.symbol ?? "—"}</td>
                      <td>{r.status === "error" ? <Badge tone="error">ERROR</Badge> : <Badge tone={r.passed ? "ok" : "idle"}>{r.passed ? "PASS" : "REJECT"}</Badge>}</td>
                      <td className="num text-right">{r.score?.toFixed(2) ?? "—"}</td>
                      <td className="num text-right">{r.duration_ms.toFixed(1)}</td>
                      <td className="text-xs text-zinc-400 whitespace-normal min-w-[300px]">{r.error ?? r.reasons.join(" · ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {runs.data!.length === 0 && <p className="text-sm text-zinc-500 p-4">No executions recorded.</p>}
            </div>
          )}
        </Section>
      )}
    </div>
  );
}
