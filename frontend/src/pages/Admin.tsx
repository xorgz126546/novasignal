import { useState } from "react";
import { Badge, ErrorBox, Loading, PageHeader, Section, Stat } from "../components/ui";
import { api, download } from "../lib/api";
import { ago, dt } from "../lib/format";
import { useFetch } from "../lib/useFetch";

export default function Admin() {
  const stats = useFetch<any>("/api/admin/stats", 30000);
  const users = useFetch<any[]>("/api/admin/users");
  const sys = useFetch<any>("/api/system/status", 30000);
  const errors = useFetch<any[]>("/api/admin/errors?limit=30");
  const cycles = useFetch<any[]>("/api/admin/cycles?limit=15");
  const opps = useFetch<any[]>("/api/admin/opportunities?limit=30");
  const audit = useFetch<any[]>("/api/admin/audit?limit=30");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const act = async (path: string, ok: (r: any) => string, body?: any) => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await api(path, { method: body ? "PATCH" : "POST", json: body });
      setMsg(ok(r));
      [stats, users, sys, errors, cycles, opps, audit].forEach((x) => x.reload());
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  if (stats.loading && !stats.data) return <Loading />;
  if (stats.error) return <ErrorBox error={stats.error} />;
  const s = stats.data!;
  return (
    <div className="space-y-5">
      <PageHeader title="Admin" subtitle="Operations, users, subscriptions and audit trail. All actions are logged."
        actions={<>
          <button className="btn-primary" disabled={busy} onClick={() => act("/api/admin/scan", (r) => `Scan ${r.status}: ${r.candidates} candidates, ${r.approved} approved. ${r.notes || r.error || ""}`)}>{busy ? "Working…" : "Run scan now"}</button>
          <button className="btn-ghost" disabled={busy} onClick={() => act("/api/admin/track", (r) => `Tracker resolved ${r.resolved} signal(s)`)}>Run tracker</button>
          {s.paused ? <button className="btn-ghost" onClick={() => act("/api/admin/resume", () => "Signal generation resumed")}>Resume generation</button>
            : <button className="btn-ghost" onClick={() => act("/api/admin/pause", () => "Signal generation paused")}>Pause generation</button>}
        </>} />
      {msg && <div className="rounded-xl border border-up/40 bg-up/10 text-up text-sm px-4 py-3">{msg}</div>}
      {err && <ErrorBox error={err} />}

      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <Stat label="Users" value={s.users} sub={`${s.active_users_7d} active 7d`} />
        <Stat label="Telegram linked" value={s.telegram_linked} />
        <Stat label="Paying subs" value={s.subscriptions.active ?? 0} sub={`target ${s.target.subscribers}`} />
        <Stat label="MRR" value={`$${s.mrr_usd}`} sub={`target $${s.target.mrr_usd.toLocaleString()}`} />
        <Stat label="Scan cycles 7d" value={s.scan_cycles_7d} />
        <Stat label="Agent errors 7d" value={s.agent_errors_7d} tone={s.agent_errors_7d ? "text-down" : ""} />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Services & market data">
          <div className="space-y-2">
            {(sys.data?.services ?? []).map((v: any) => (
              <div key={v.name} className="flex items-center justify-between text-sm gap-2">
                <span className="capitalize">{v.name}</span>
                <span className="flex items-center gap-2 text-xs text-zinc-500">ok {ago(v.last_ok_at)}<Badge>{v.status}</Badge>
                  <button className="text-zinc-400 hover:text-accent" onClick={() => act(`/api/admin/services/${v.name}/restart`, () => `${v.name} restarted`)}>restart</button></span>
              </div>
            ))}
            {sys.data && <p className="text-xs text-zinc-500 pt-2 border-t border-graphite-700">Binance: {sys.data.binance.base_url} · last OK {ago(sys.data.binance.last_ok_at)} {sys.data.binance.last_error && <span className="text-down">· {sys.data.binance.last_error}</span>}</p>}
            <div className="flex gap-2 pt-1">
              {["binance", "telegram", "scheduler"].map((n) => <button key={n} className="btn-ghost text-xs px-3 py-1" onClick={() => act(`/api/admin/services/${n}/restart`, () => `${n} restarted`)}>Restart {n}</button>)}
            </div>
            <div className="pt-3 border-t border-graphite-700">
              <button className="btn-ghost text-xs px-3 py-1" disabled={busy || !window.location.origin.startsWith("https://")}
                onClick={() => act(`/api/telegram/setup-webhook?public_api_url=${encodeURIComponent(window.location.origin)}`, (r) => `Telegram webhook connected for @${r.bot}`)}>
                Connect Telegram webhook
              </button>
              <p className="text-[11px] text-zinc-500 mt-1">Registers {window.location.origin}/api/telegram/webhook with Telegram (requires HTTPS and TELEGRAM_BOT_TOKEN on the server).</p>
            </div>
          </div>
        </Section>
        <Section title="Exports">
          <div className="flex flex-wrap gap-2">
            {["signals", "paper_trades", "user_trades", "opportunities"].map((k) => (
              <button key={k} className="btn-ghost" onClick={() => download(`/api/admin/export/${k}.csv`, `cryptonex_${k}.csv`)}>{k.replace("_", " ")} .csv</button>
            ))}
          </div>
          <p className="text-xs text-zinc-500 mt-3">Subscriptions: {Object.entries(s.subscriptions).map(([k, v]) => `${k} ${v}`).join(" · ") || "none"}. Paid billing is disabled until payments, legal and reliability are verified.</p>
        </Section>
      </div>

      <Section title="Users">
        <div className="overflow-x-auto -mx-4 sm:mx-0">
          <table className="table">
            <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Subscription</th><th>Telegram</th><th>Reports</th><th>Last login</th><th></th></tr></thead>
            <tbody>{(users.data ?? []).map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td><td>{u.role}</td><td>{u.is_active ? <Badge tone="ok">ACTIVE</Badge> : <Badge tone="error">DISABLED</Badge>}</td>
                <td>
                  <select className="input py-1 w-32" value={u.subscription} onChange={(e) => act(`/api/admin/users/${u.id}`, () => "Subscription updated", { subscription_status: e.target.value })}>
                    {["none", "trial", "active", "past_due", "cancelled", "expired"].map((x) => <option key={x}>{x}</option>)}
                  </select>
                </td>
                <td>{u.telegram_linked ? "yes" : "—"}</td><td className="num">{u.reports}</td><td className="text-zinc-400">{ago(u.last_login_at)}</td>
                <td className="text-right space-x-3">
                  <button className="text-xs text-zinc-400 hover:text-accent" onClick={() => act(`/api/admin/users/${u.id}`, () => "User updated", { is_active: !u.is_active })}>{u.is_active ? "disable" : "enable"}</button>
                  <button className="text-xs text-zinc-400 hover:text-accent" onClick={() => act(`/api/admin/users/${u.id}`, () => "Role updated", { role: u.role === "admin" ? "user" : "admin" })}>{u.role === "admin" ? "make user" : "make admin"}</button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Recent scan cycles">
          <div className="space-y-2 text-sm max-h-80 overflow-y-auto">
            {(cycles.data ?? []).map((c) => (
              <div key={c.id} className="border-b border-graphite-700 pb-2">
                <div className="flex justify-between"><span className="text-zinc-400">#{c.id} · {dt(c.started_at)}</span><Badge tone={c.status === "ok" ? "ok" : c.status === "error" ? "error" : "idle"}>{c.status}</Badge></div>
                <div className="text-xs text-zinc-500 mt-1">{c.candidates} candidates · {c.approved} approved · {c.rejected} rejected {c.error && <span className="text-down">· {c.error}</span>}</div>
              </div>
            ))}
          </div>
        </Section>
        <Section title="Recent opportunities (approved & rejected)">
          <div className="space-y-2 text-sm max-h-80 overflow-y-auto">
            {(opps.data ?? []).map((o) => (
              <div key={o.id} className="border-b border-graphite-700 pb-2">
                <div className="flex justify-between"><span>{o.symbol} <span className="text-zinc-500 text-xs">conf {o.confidence?.toFixed(0) ?? "—"}</span></span><Badge tone={o.decision === "approved" ? "ok" : "idle"}>{o.decision}</Badge></div>
                {o.reasons?.length > 0 && <div className="text-xs text-zinc-500 mt-1 truncate">{o.reasons.slice(0, 2).join(" · ")}</div>}
              </div>
            ))}
          </div>
        </Section>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="System errors">
          <div className="space-y-2 text-xs max-h-80 overflow-y-auto">
            {(errors.data ?? []).length === 0 ? <p className="text-zinc-500 text-sm">No errors recorded.</p> : errors.data!.map((e, i) => (
              <div key={i} className="border-b border-graphite-700 pb-2"><span className="text-zinc-400">{dt(e.at)} · {e.source}{e.symbol ? ` · ${e.symbol}` : ""}</span><div className="text-down mt-0.5 break-all">{e.error}</div></div>
            ))}
          </div>
        </Section>
        <Section title="Audit log">
          <div className="space-y-1.5 text-xs max-h-80 overflow-y-auto">
            {(audit.data ?? []).map((a) => (
              <div key={a.id} className="flex gap-3"><span className="text-zinc-500 w-28 shrink-0">{dt(a.at)}</span><span className="text-zinc-400 w-20 shrink-0 truncate">{a.actor}</span><span>{a.action}</span></div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}
