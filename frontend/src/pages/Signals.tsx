import { useState } from "react";
import { Link } from "react-router-dom";
import ReportDialog from "../components/ReportDialog";
import { Badge, Bar, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { dt, pct, price, signClass, until } from "../lib/format";
import { useFetch } from "../lib/useFetch";

const OPEN = "ACTIVE,USER_ENTERED,USER_SKIPPED,APPROVED";

export function SignalCard({ s, onReport }: { s: any; onReport: (a: "ENTERED" | "SKIPPED" | "CLOSED") => void }) {
  const live = ["ACTIVE", "USER_ENTERED", "USER_SKIPPED", "APPROVED"].includes(s.status);
  return (
    <div className="card card-pad flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Link to={`/app/signals/${s.id}`} className="text-lg font-bold hover:text-accent">{s.pair}</Link>
          <div className="text-xs text-zinc-500 mt-0.5">#{s.id} · BUY · {dt(s.created_at)}</div>
        </div>
        <Badge>{s.status}</Badge>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {[["Entry", s.entry_price, "text-zinc-100"], ["Take profit", s.take_profit, "text-up"], ["Stop loss", s.stop_loss, "text-down"]].map(([l, v, c]) => (
          <div key={l as string} className="rounded-xl bg-graphite-900 border border-graphite-700 px-3 py-2">
            <div className="label">{l}</div>
            <div className={`num text-sm font-semibold mt-1 ${c}`}>{price(v as number)}</div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-3 gap-2 text-sm">
        <div><div className="label">Move</div><div className="num mt-1 text-up">{pct(s.expected_move_pct)}</div></div>
        <div><div className="label">Net est.</div><div className="num mt-1">{pct(s.net_expected_return_pct)}</div></div>
        <div><div className="label">R/R</div><div className="num mt-1">1:{s.risk_reward.toFixed(2)}</div></div>
      </div>
      <div>
        <div className="flex justify-between text-xs mb-1.5"><span className="text-zinc-500">Confidence score (evidence, not probability)</span><span className="num">{s.confidence.toFixed(0)}/100</span></div>
        <Bar value={s.confidence} tone="bg-zinc-200" />
      </div>
      {s.justification?.length > 0 && (
        <ul className="text-xs text-zinc-400 space-y-1 line-clamp-4">
          {s.justification.slice(0, 3).map((j: string, i: number) => <li key={i} className="truncate">{j}</li>)}
        </ul>
      )}
      <div className="flex items-center justify-between text-xs text-zinc-500 border-t border-graphite-700 pt-3">
        <span>Entry valid {until(s.entry_valid_until)}</span>
        <span>Expires {until(s.expires_at)}</span>
      </div>
      {s.theoretical_outcome && (
        <div className="text-xs text-zinc-400">Outcome: <Badge>{s.theoretical_outcome}</Badge> <span className={`num ml-1 ${signClass(s.theoretical_return_pct)}`}>{pct(s.theoretical_return_pct)}</span></div>
      )}
      <div className="flex gap-2 flex-wrap">
        {!s.my_decision && live && (
          <>
            <button className="btn-primary flex-1" onClick={() => onReport("ENTERED")}>I entered</button>
            <button className="btn-ghost flex-1" onClick={() => onReport("SKIPPED")}>Skip</button>
          </>
        )}
        {s.my_decision === "ENTERED" && s.my_trade_status === "OPEN" && <button className="btn-primary flex-1" onClick={() => onReport("CLOSED")}>Report exit</button>}
        {s.my_decision && <span className="text-xs text-zinc-500 self-center">You: {s.my_decision.toLowerCase()}{s.my_trade_status === "CLOSED" ? " · closed" : ""}</span>}
      </div>
    </div>
  );
}

export default function Signals() {
  const [tab, setTab] = useState<"open" | "history">("open");
  const { data, error, loading, reload } = useFetch<any[]>(tab === "open" ? `/api/signals?status=${OPEN}` : "/api/signals?limit=100", 20000);
  const [dlg, setDlg] = useState<{ s: any; a: "ENTERED" | "SKIPPED" | "CLOSED" } | null>(null);
  return (
    <div>
      <PageHeader title="Live Signals" subtitle="Maximum 3 qualified signals per day. Zero signals is a normal outcome when no setup meets every rule."
        actions={
          <div className="flex rounded-xl border border-graphite-600 p-1">
            {(["open", "history"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`px-4 py-1.5 rounded-lg text-sm capitalize ${tab === t ? "bg-graphite-600 text-white" : "text-zinc-400"}`}>{t}</button>
            ))}
          </div>
        } />
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} /> : !data?.length ? (
        <div className="card"><Empty title={tab === "open" ? "No open signals right now" : "No signals yet"}>The agents scan continuously. When no opportunity passes all 11 agents, nothing is published — by design.</Empty></div>
      ) : (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.map((s) => <SignalCard key={s.id} s={s} onReport={(a) => setDlg({ s, a })} />)}
        </div>
      )}
      {dlg && <ReportDialog signal={dlg.s} action={dlg.a} onClose={() => setDlg(null)} onDone={reload} />}
    </div>
  );
}
