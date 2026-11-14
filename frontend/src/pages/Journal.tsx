import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Section, Stat } from "../components/ui";
import { api } from "../lib/api";
import { dt, pct, price, signClass } from "../lib/format";
import { useFetch } from "../lib/useFetch";

export default function Journal() {
  const { data, error, loading, reload } = useFetch<any[]>("/api/trades");
  const [editing, setEditing] = useState<number | null>(null);
  const [notes, setNotes] = useState("");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const rows = data ?? [];
  const closed = rows.filter((r) => r.status === "CLOSED");
  const pnl = closed.reduce((a, r) => a + (r.realized_pnl ?? 0), 0);
  const fees = rows.reduce((a, r) => a + (r.fees ?? 0), 0);
  const save = async (id: number) => {
    await api(`/api/trades/${id}`, { method: "PATCH", json: { notes } });
    setEditing(null);
    reload();
  };
  return (
    <div className="space-y-5">
      <PageHeader title="Trade Journal" subtitle="Only what you reported. A signal never counts as a trade unless you confirmed you entered it." />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Entered" value={rows.filter((r) => r.decision === "ENTERED").length} />
        <Stat label="Skipped" value={rows.filter((r) => r.decision === "SKIPPED").length} />
        <Stat label="Realized P/L" value={`${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} USDT`} tone={signClass(pnl)} sub={`${closed.length} closed trades`} />
        <Stat label="Fees paid" value={`${fees.toFixed(2)} USDT`} />
      </div>
      <Section title="All reports">
        {rows.length === 0 ? <Empty title="No reports yet">Use “I entered” or “Skip” on a signal, here or in Telegram.</Empty> : (
          <div className="overflow-x-auto -mx-4 sm:mx-0">
            <table className="table">
              <thead><tr><th>Pair</th><th>Decision</th><th>Status</th><th className="text-right">Signal entry</th><th className="text-right">Your entry</th><th className="text-right">Slippage</th><th className="text-right">Exit</th><th className="text-right">Fees</th><th className="text-right">P/L</th><th className="text-right">Return</th><th className="text-right">Duration</th><th>Notes</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="hover:bg-graphite-700/40">
                    <td><Link className="font-medium hover:text-accent" to={`/app/signals/${r.signal_id}`}>{r.pair}</Link><div className="text-[11px] text-zinc-500">{dt(r.created_at)}</div></td>
                    <td>{r.decision}</td>
                    <td><Badge>{r.status}</Badge></td>
                    <td className="num text-right">{price(r.signal_entry)}</td>
                    <td className="num text-right">{price(r.entry_price)}</td>
                    <td className="num text-right">{pct(r.slippage_pct, 3)}</td>
                    <td className="num text-right">{price(r.exit_price)}</td>
                    <td className="num text-right">{r.fees ? r.fees.toFixed(2) : "—"}</td>
                    <td className={`num text-right ${signClass(r.realized_pnl)}`}>{r.realized_pnl != null ? `${r.realized_pnl >= 0 ? "+" : ""}${r.realized_pnl.toFixed(2)}` : "—"}</td>
                    <td className={`num text-right ${signClass(r.realized_pct)}`}>{pct(r.realized_pct)}</td>
                    <td className="num text-right">{r.duration_hours != null ? `${r.duration_hours.toFixed(1)}h` : "—"}</td>
                    <td className="max-w-[240px]">
                      {editing === r.id ? (
                        <div className="flex gap-1"><input className="input py-1" value={notes} onChange={(e) => setNotes(e.target.value)} /><button className="btn-ghost px-2 py-1" onClick={() => save(r.id)}>Save</button></div>
                      ) : (
                        <button className="text-left text-zinc-400 hover:text-zinc-100 truncate max-w-[240px] block" onClick={() => { setEditing(r.id); setNotes(r.notes || ""); }}>{r.notes || "add note"}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
