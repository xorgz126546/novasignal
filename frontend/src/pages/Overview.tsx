import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Section, Stat } from "../components/ui";
import { ago, pct, price, rate, ratio, signClass } from "../lib/format";
import { useFetch } from "../lib/useFetch";

const TRACKS: [string, string, string][] = [
  ["theoretical", "Theoretical", "Signal levels, no costs"],
  ["paper", "Paper trading", "Simulated fees, slippage, delay"],
  ["actual", "Your actual trades", "User-confirmed fills only"],
];

export default function Overview() {
  const { data, error, loading } = useFetch<any>("/api/overview", 30000);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const d = data!;
  const paper = d.tracks.paper;
  return (
    <div className="space-y-6">
      <PageHeader title="Overview" subtitle="Live state of the signal engine and your performance. Each performance track is computed separately and never combined."
        actions={d.paused ? <Badge tone="degraded">GENERATION PAUSED</Badge> : <Badge tone="ok">ENGINE ACTIVE</Badge>} />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Opportunities evaluated" value={d.opportunities_evaluated} sub="every candidate, approved or rejected" />
        <Stat label="Signals approved" value={d.signals_approved} sub={`${d.open_signals} open now`} />
        <Stat label="Paper win rate" value={rate(paper.win_rate)} sub={`${paper.trades} closed · loss rate ${rate(paper.loss_rate)}`} />
        <Stat label="Paper net expectancy" value={pct(paper.expectancy_pct)} tone={signClass(paper.expectancy_pct)} sub="per trade, after fees & slippage" />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        {TRACKS.map(([k, label, sub]) => {
          const m = d.tracks[k];
          return (
            <Section key={k} title={label} right={<span className="text-[11px] text-zinc-500">{sub}</span>}>
              {m.trades === 0 ? (
                <p className="text-sm text-zinc-500">No closed trades yet.</p>
              ) : (
                <dl className="grid grid-cols-2 gap-y-3 text-sm">
                  <dt className="text-zinc-500">Trades</dt><dd className="num text-right">{m.trades}</dd>
                  <dt className="text-zinc-500">Win rate</dt><dd className="num text-right">{rate(m.win_rate)}</dd>
                  <dt className="text-zinc-500">Sum of returns</dt><dd className={`num text-right ${signClass(m.sum_return_pct)}`}>{pct(m.sum_return_pct)}</dd>
                  <dt className="text-zinc-500">Profit factor</dt><dd className="num text-right">{ratio(m.profit_factor)}</dd>
                  <dt className="text-zinc-500">Max drawdown</dt><dd className="num text-right">{pct(m.max_drawdown_pct, 2, false)}</dd>
                </dl>
              )}
              <div className="grid grid-cols-3 gap-2 mt-4 pt-4 border-t border-graphite-700">
                {["day", "week", "month"].map((p) => {
                  const pm = d.periods[k][p];
                  return (
                    <div key={p} className="text-center">
                      <div className="label">{p}</div>
                      <div className={`num text-sm mt-1 ${signClass(pm.sum_return_pct)}`}>{pm.trades ? pct(pm.sum_return_pct) : "—"}</div>
                    </div>
                  );
                })}
              </div>
            </Section>
          );
        })}
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Section title="Current open position" className="lg:col-span-1">
          {d.current_position ? (
            <Link to={`/app/signals/${d.current_position.signal_id}`} className="block space-y-2">
              <div className="text-lg font-semibold">{d.current_position.pair}</div>
              <div className="grid grid-cols-2 gap-y-1 text-sm">
                <span className="text-zinc-500">Your entry</span><span className="num text-right">{price(d.current_position.entry_price)}</span>
                <span className="text-zinc-500">Take profit</span><span className="num text-right">{price(d.current_position.take_profit)}</span>
                <span className="text-zinc-500">Stop loss</span><span className="num text-right">{price(d.current_position.stop_loss)}</span>
                <span className="text-zinc-500">Last price</span><span className="num text-right">{price(d.current_position.last_price)}</span>
                <span className="text-zinc-500">Unrealized</span><span className={`num text-right ${signClass(d.current_position.unrealized_pct)}`}>{pct(d.current_position.unrealized_pct)}</span>
              </div>
            </Link>
          ) : (
            <Empty title="No open position">Positions appear only after you report ENTERED on a signal.</Empty>
          )}
        </Section>

        <Section title="Agent status" className="lg:col-span-2" right={<Link to="/app/agents" className="text-xs text-zinc-400 hover:text-accent">Monitor →</Link>}>
          <div className="grid sm:grid-cols-2 gap-x-6 gap-y-2">
            {d.agents.map((a: any) => (
              <div key={a.id} className="flex items-center justify-between text-sm py-1 border-b border-graphite-700/60">
                <span className="truncate"><span className="num text-zinc-500 mr-2">{String(a.number).padStart(2, "0")}</span>{a.name}</span>
                <span className="flex items-center gap-2 shrink-0"><span className="text-[11px] text-zinc-500">{ago(a.last_run)}</span><Badge>{a.status}</Badge></span>
              </div>
            ))}
          </div>
          {d.last_scan && (
            <p className="text-xs text-zinc-500 mt-4">Last scan {ago(d.last_scan.at)} · {d.last_scan.status} · {d.last_scan.candidates} candidates · {d.last_scan.approved} approved — {d.last_scan.notes}</p>
          )}
        </Section>
      </div>
    </div>
  );
}
