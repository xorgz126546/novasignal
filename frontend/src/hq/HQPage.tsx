import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, ErrorBox, Loading } from "../components/ui";
import { api, download } from "../lib/api";
import { ago, dt } from "../lib/format";
import { useLang } from "../lib/i18n";
import { useFetch } from "../lib/useFetch";
import Markdown from "./Markdown";
import Office, { type OfficeAgent, type OfficeHandle } from "./Office";
import { useHQStream, type HQEvent } from "./useHQStream";

type Agent = OfficeAgent & { role: string; xp: number; next_level_xp: number | null; model: string; tools: Record<string, { min_level: number; unlocked: boolean }>; last_active_at: string | null };
const TABS = ["office", "needs_you", "reports", "knowledge", "drafts", "activity"] as const;

export default function HQPage() {
  const { t, lang } = useLang();
  const [tab, setTab] = useState<(typeof TABS)[number]>("office");
  const { data: initial, error } = useFetch<Agent[]>("/api/hq/agents");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [feed, setFeed] = useState<HQEvent[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const office = useRef<OfficeHandle>(null);
  const status = useFetch<any>("/api/hq/status", 60000);

  useEffect(() => { if (initial) setAgents(initial); }, [initial]);
  useEffect(() => { api<any[]>("/api/hq/approvals").then((a) => setPending(a.length)).catch(() => {}); }, [tab]);

  const onEvent = useCallback((e: HQEvent) => {
    setFeed((f) => [e, ...f].slice(0, 80));
    if (e.type === "agent_status" && e.agent_id) {
      setAgents((as) => as.map((a) => (a.id === e.agent_id ? { ...a, status: e.data.status, current_task: e.data.task ?? "" } : a)));
    }
    if ((e.type === "xp" || e.type === "level_up") && e.agent_id) {
      setAgents((as) => as.map((a) => (a.id === e.agent_id ? { ...a, xp: e.data.total ?? a.xp, level: e.data.level ?? a.level } : a)));
    }
    if (e.type === "handover" && e.agent_id) office.current?.handover(e.agent_id);
    if (e.type === "needs_you") setPending((p) => p + 1);
    if (e.type === "approval_decided") setPending((p) => Math.max(0, p - 1));
  }, []);
  const live = useHQStream(onEvent);

  if (error) return <ErrorBox error={error} />;
  if (!initial) return <Loading />;
  const working = agents.filter((a) => a.status === "working").length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-3">
            {t("hq")}
            <span className={`text-[11px] px-2 py-0.5 rounded-md border font-bold ${live ? "border-up/50 text-up bg-up/10" : "border-zinc-600 text-zinc-500"}`}>
              ● {live ? t("live") : t("offline")}
            </span>
          </h1>
          <p className="text-xs text-zinc-500 mt-1">{agents.length} agents · {working} {t("working")} · {t("budget")}: ${status.data?.llm?.spent_24h_usd ?? 0} / ${status.data?.llm?.cap_usd ?? "—"}</p>
        </div>
        <div className="flex flex-wrap gap-1 rounded-xl border border-graphite-600 p-1">
          {TABS.map((k) => (
            <button key={k} onClick={() => setTab(k)} className={`px-3 py-1.5 rounded-lg text-sm ${tab === k ? "bg-graphite-600 text-white" : "text-zinc-400"}`}>
              {t(k)}{k === "needs_you" && pending > 0 && <span className="ms-1 rounded-full bg-accent px-1.5 text-[10px] text-white">{pending}</span>}
            </button>
          ))}
        </div>
      </div>
      {status.data && !status.data.hq_enabled && <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-200 text-xs px-3 py-2">{t("hq_off")}</div>}

      {tab === "office" && (
        <div className="grid xl:grid-cols-[1fr_380px] gap-4">
          <div className="space-y-3">
            <div className="card p-2"><Office ref={office} agents={agents} lang={lang} onSelect={setSel} selected={sel} /></div>
            <Controls />
          </div>
          <div className="space-y-4">
            {sel ? <AgentPanel id={sel} onClose={() => setSel(null)} /> : <Chat />}
            {sel && <Chat />}
            <Feed events={feed} agents={agents} />
          </div>
        </div>
      )}
      {tab === "needs_you" && <NeedsYou />}
      {tab === "reports" && <Reports />}
      {tab === "knowledge" && <Knowledge />}
      {tab === "drafts" && <Drafts />}
      {tab === "activity" && <Activity />}
      <p className="text-[11px] text-zinc-500">{t("disclaimer")}</p>
    </div>
  );
}

function Controls() {
  const { t } = useLang();
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (path: string, label: string, body?: unknown) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api<any>(path, { method: "POST", json: body });
      setMsg(`${label}: ${r.signals ? `${r.signals.length} signal(s)` : ""} ${r.no_trade?.length ? "NO TRADE — " + r.no_trade[0] : ""} ${r.status ?? ""}`.trim());
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="flex flex-wrap gap-2 items-center">
      <button className="btn-primary" disabled={busy} onClick={() => run("/api/hq/cycle", t("run_cycle"))}>{t("run_cycle")}</button>
      <button className="btn-ghost" disabled={busy} onClick={() => run("/api/hq/audit", t("run_audit"))}>{t("run_audit")}</button>
      <button className="btn-ghost" disabled={busy} onClick={() => run("/api/hq/growth", t("run_growth"))}>{t("run_growth")}</button>
      <button className="btn-ghost" disabled={busy} onClick={() => run("/api/hq/brief", t("make_brief"))}>{t("make_brief")}</button>
      <button className="btn-ghost text-amber-300" disabled={busy} onClick={() => confirm("Inject a test failure into the Binance feed?") && run("/api/hq/inject-failure", t("inject"), { calls: 20 })}>{t("inject")}</button>
      {msg && <span className="text-xs text-zinc-400" dir="auto">{msg}</span>}
    </div>
  );
}

function AgentPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, lang } = useLang();
  const { data: a, loading } = useFetch<any>(`/api/hq/agents/${id}`, 15000);
  if (loading || !a) return <div className="card card-pad"><Loading /></div>;
  const pct = a.next_level_xp ? Math.min(100, (a.xp / a.next_level_xp) * 100) : 100;
  return (
    <div className="card card-pad space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-bold text-lg" style={{ color: a.color }}>{a.name[lang] ?? a.name.en}</div>
          <p className="text-xs text-zinc-400" dir="ltr">{a.role}</p>
        </div>
        <button className="text-zinc-500 hover:text-white" onClick={onClose}>✕</button>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-amber-300 font-bold">{t("level")} {a.level}</span>
        <div className="flex-1 h-2 rounded-full bg-graphite-600 overflow-hidden"><div className="h-full bg-amber-300" style={{ width: `${pct}%` }} /></div>
        <span className="num text-xs text-zinc-400">{a.xp}{a.next_level_xp ? ` / ${a.next_level_xp}` : ""} XP</span>
      </div>
      <div className="flex flex-wrap gap-1">
        <Badge tone={a.status === "working" ? "ok" : a.status === "blocked" ? "error" : "idle"}>{t(a.status)}</Badge>
        <span className="text-[11px] text-zinc-500 self-center">{a.model} · {a.schedule}</span>
      </div>
      <div className="text-xs"><div className="label mb-1">{t("tools")}</div>
        <div className="flex flex-wrap gap-1">{Object.entries(a.tools).map(([k, v]: any) => (
          <span key={k} className={`px-1.5 py-0.5 rounded border ${v.unlocked ? "border-up/40 text-up" : "border-zinc-600 text-zinc-500"}`}>{v.unlocked ? "✓" : "🔒"} {k} (Lv{v.min_level})</span>))}</div>
      </div>
      <details open><summary className="label cursor-pointer">{t("tasks")}</summary>
        <ul className="mt-2 space-y-1 text-xs">{a.tasks.slice(0, 8).map((x: any) => (
          <li key={x.id} className="flex justify-between gap-2"><span className="truncate" dir="auto">{x.title}</span><span className={x.status === "failed" ? "text-down" : "text-zinc-500"}>{x.status} · {ago(x.created_at)}</span></li>))}
          {!a.tasks.length && <li className="text-zinc-500">—</li>}</ul>
      </details>
      {a.xp_events?.length > 0 && <div className="text-xs"><div className="label mb-1">XP</div>{a.xp_events.slice(0, 5).map((x: any, i: number) => <div key={i} className="text-zinc-400">+{x.xp} {x.source_type} — {x.note}</div>)}</div>}
      <details><summary className="label cursor-pointer">{t("logs")}</summary>
        <ul className="mt-2 space-y-1 text-[11px] text-zinc-400 max-h-48 overflow-y-auto">{a.logs.map((e: any) => <li key={e.id} dir="auto">{dt(e.at)} · {e.type} {e.data?.task || e.data?.title || e.data?.summary || ""}</li>)}</ul>
      </details>
      <details><summary className="label cursor-pointer">{t("skills")}</summary><div className="mt-2">{a.skills_md ? <Markdown text={a.skills_md} /> : <p className="text-xs text-zinc-500">— (written after the weekly Library session, with your approval)</p>}</div></details>
      {a.latest_report && <details><summary className="label cursor-pointer">{t("latest_report")}</summary><div className="mt-2"><Markdown text={a.latest_report.body_md} /></div></details>}
    </div>
  );
}

function Chat() {
  const { t } = useLang();
  const [msgs, setMsgs] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { api<any[]>("/api/hq/chat").then(setMsgs).catch(() => {}); }, []);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [msgs]);
  const send = async () => {
    if (!text.trim()) return;
    const mine = { id: Date.now(), role: "owner", text };
    setMsgs((m) => [...m, mine]); setText(""); setBusy(true);
    try { const r = await api<any>("/api/hq/chat", { method: "POST", json: { text: mine.text } }); setMsgs((m) => [...m, r]); }
    catch (e: any) { setMsgs((m) => [...m, { id: Date.now() + 1, role: "manager", text: `⚠ ${e.message}` }]); }
    finally { setBusy(false); }
  };
  return (
    <div className="card card-pad">
      <div className="font-semibold text-sm mb-2">{t("chat_title")}</div>
      <div className="h-64 overflow-y-auto space-y-2 pe-1">
        {msgs.map((m) => (
          <div key={m.id} className={`flex ${m.role === "owner" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "owner" ? "bg-accent text-white" : "bg-graphite-700 text-zinc-100"}`} dir="auto">{m.text}</div>
          </div>
        ))}
        {busy && <div className="text-xs text-zinc-500">…</div>}
        <div ref={end} />
      </div>
      <div className="flex gap-2 mt-2">
        <input className="input" value={text} placeholder={t("chat_ph")} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} dir="auto" />
        <button className="btn-primary" disabled={busy} onClick={send}>{t("send")}</button>
      </div>
    </div>
  );
}

function Feed({ events, agents }: { events: HQEvent[]; agents: Agent[] }) {
  const { lang } = useLang();
  const name = (id: string | null) => (id ? (agents.find((a) => a.id === id)?.name[lang] ?? id).split(" — ")[0] : "HQ");
  return (
    <div className="card card-pad">
      <div className="label mb-2">Live</div>
      <ul className="space-y-1 text-[11px] max-h-56 overflow-y-auto">
        {events.filter((e) => e.type !== "agent_status").map((e) => (
          <li key={e.id} className="text-zinc-400" dir="auto"><span className="text-zinc-600">{new Date(e.at).toLocaleTimeString()}</span> <b className="text-zinc-200">{name(e.agent_id)}</b> {e.type}{" "}
            {e.data.title || e.data.summary || e.data.symbol || e.data.text || (e.data.reasons ?? []).join("; ")}</li>
        ))}
        {!events.length && <li className="text-zinc-600">waiting for activity…</li>}
      </ul>
    </div>
  );
}

function NeedsYou() {
  const { t } = useLang();
  const ap = useFetch<any[]>("/api/hq/approvals");
  const cl = useFetch<any[]>("/api/hq/checklist");
  const vs = useFetch<any[]>("/api/hq/versions");
  const [err, setErr] = useState<string | null>(null);
  const act = async (path: string, body?: unknown) => {
    setErr(null);
    try { await api(path, { method: "POST", json: body ?? {} }); ap.reload(); vs.reload(); cl.reload(); } catch (e: any) { setErr(e.message); }
  };
  const activeKeys = Array.from(new Set((vs.data ?? []).filter((v) => v.active).map((v) => v.key)));
  return (
    <div className="grid lg:grid-cols-2 gap-4">
      <div className="space-y-3">
        {err && <ErrorBox error={err} />}
        {(ap.data ?? []).map((a) => (
          <div key={a.id} className="card card-pad space-y-2">
            <div className="flex justify-between gap-2"><b dir="auto">{a.title}</b><Badge>{a.kind}</Badge></div>
            <div className="text-sm text-zinc-300 whitespace-pre-wrap" dir="auto">{a.summary}</div>
            {a.payload?.gate?.checks && <div className="flex flex-wrap gap-1">{Object.entries(a.payload.gate.checks).map(([k, v]: any) => <Badge key={k} tone={v ? "ok" : "error"}>{k}</Badge>)}</div>}
            {a.payload?.pr_url && <a className="text-accent text-sm underline" href={a.payload.pr_url} target="_blank" rel="noreferrer">Open the draft PR on GitHub →</a>}
            <div className="flex gap-2">
              <button className="btn-primary" onClick={() => act(`/api/hq/approvals/${a.id}/approve`)}>{t("approve")}</button>
              <button className="btn-ghost" onClick={() => act(`/api/hq/approvals/${a.id}/reject`, { reason: "" })}>{t("reject")}</button>
            </div>
          </div>
        ))}
        {ap.data && !ap.data.length && <div className="card card-pad text-zinc-500 text-sm">{t("nothing_pending")}</div>}
      </div>
      <div className="space-y-4">
        <div className="card card-pad">
          <div className="font-semibold text-sm mb-2">{t("your_part")}</div>
          {(cl.data ?? []).map((c) => (
            <label key={c.id} className="flex gap-2 items-start text-sm py-1 cursor-pointer" dir="auto">
              <input type="checkbox" checked={c.done} className="mt-1 accent-[#ff2a3d]"
                onChange={async (e) => { await api(`/api/hq/checklist/${c.id}`, { method: "PATCH", json: { done: e.target.checked } }); cl.reload(); }} />
              <span className={c.done ? "line-through text-zinc-500" : ""}>{c.text}</span>
            </label>
          ))}
        </div>
        <div className="card card-pad">
          <div className="font-semibold text-sm mb-2">{t("versions")}</div>
          {activeKeys.length === 0 && <p className="text-xs text-zinc-500">—</p>}
          {activeKeys.map((k) => {
            const v = (vs.data ?? []).find((x) => x.key === k && x.active);
            return (
              <div key={k} className="flex items-center justify-between text-sm py-1">
                <span className="truncate" dir="ltr">{k} <span className="text-zinc-500">v{v?.version}</span></span>
                <button className="btn-ghost text-xs px-2 py-1" onClick={() => act("/api/hq/versions/rollback", { key: k })}>{t("rollback")}</button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Reports() {
  const { t } = useLang();
  const { data, reload } = useFetch<any[]>("/api/hq/reports?limit=60");
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button className="btn-primary" onClick={async () => { await api("/api/hq/brief", { method: "POST" }); reload(); }}>{t("make_brief")}</button>
        <button className="btn-ghost" onClick={async () => { await api("/api/hq/reports/agents", { method: "POST" }); reload(); }}>Agent reports</button>
      </div>
      {(data ?? []).map((r) => (
        <div key={r.id} className="card card-pad">
          <button className="w-full flex justify-between gap-2 text-start" onClick={() => setOpen(open === r.id ? null : r.id)}>
            <span className="font-medium" dir="auto">{r.title}</span>
            <span className="text-xs text-zinc-500 shrink-0">{r.kind} · {r.day}{r.sent_telegram ? " · ✈" : ""}</span>
          </button>
          {open === r.id && (
            <div className="mt-3 space-y-2">
              <Markdown text={r.body_md} />
              <button className="btn-ghost text-xs" onClick={() => download(`/api/hq/reports/${r.id}.md`, `${r.day}-${r.kind}-${r.id}.md`)}>{t("download")}</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Knowledge() {
  const { t } = useLang();
  const k = useFetch<any[]>("/api/hq/knowledge");
  const p = useFetch<any[]>("/api/hq/proposals");
  const bt = useFetch<any[]>("/api/hq/backtests");
  const [topic, setTopic] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <input className="input" placeholder={t("learn")} value={topic} onChange={(e) => setTopic(e.target.value)} dir="auto" />
        <button className="btn-primary" onClick={async () => { setMsg("…"); try { const r = await api<any>("/api/hq/scout", { method: "POST", json: { topic } }); setMsg(`${r.knowledge_ids.length} note(s)`); k.reload(); p.reload(); } catch (e: any) { setMsg(e.message); } }}>{t("send")}</button>
        <button className="btn-ghost shrink-0" onClick={() => download("/api/hq/provenance.md", "PROVENANCE.md")}>PROVENANCE.md</button>
      </div>
      {msg && <p className="text-xs text-zinc-400">{msg}</p>}
      <div className="grid lg:grid-cols-2 gap-3">
        {(k.data ?? []).map((n) => (
          <details key={n.id} className="card card-pad">
            <summary className="cursor-pointer flex justify-between gap-2"><span dir="auto">{n.title}</span>
              <span className="flex gap-1 shrink-0"><Badge tone={n.license_class === "adaptable" ? "ok" : "degraded"}>{n.license}</Badge>{n.injection_flags?.length > 0 && <Badge tone="error">⚠ injection</Badge>}</span></summary>
            <div className="mt-2"><Markdown text={n.body_md} /></div>
          </details>
        ))}
      </div>
      <div className="card card-pad">
        <div className="label mb-2">Proposals & backtest gate</div>
        {(p.data ?? []).map((x) => <div key={x.id} className="text-sm flex justify-between gap-2 py-1"><span dir="auto">[{x.kind}] {x.title}</span><Badge tone={x.status === "passed" || x.status === "approved" ? "ok" : x.status === "failed" || x.status === "rejected" ? "error" : "idle"}>{x.status}</Badge></div>)}
        {(bt.data ?? []).slice(0, 10).map((r) => <div key={r.id} className="text-xs text-zinc-400 py-0.5" dir="ltr">{r.subject}: {r.passed ? "PASS" : "FAIL"} · {r.summary?.trades} trades · exp {r.summary?.expectancy_pct?.toFixed?.(2) ?? "—"}%</div>)}
      </div>
    </div>
  );
}

function Drafts() {
  const { t } = useLang();
  const { data } = useFetch<any[]>("/api/hq/drafts");
  const [svgs, setSvgs] = useState<Record<number, string>>({});
  useEffect(() => {
    (data ?? []).filter((d) => d.kind === "signal_card" && !svgs[d.id]).forEach(async (d) => {
      const blob = new Blob([d.body], { type: "image/svg+xml" });
      setSvgs((s) => ({ ...s, [d.id]: URL.createObjectURL(blob) }));
    });
  }, [data]); // eslint-disable-line
  return (
    <div className="grid lg:grid-cols-2 gap-3">
      {(data ?? []).map((d) => (
        <div key={d.id} className="card card-pad space-y-2">
          <div className="flex justify-between"><b dir="auto">{d.title}</b><Badge tone={d.status === "approved" ? "ok" : d.status === "rejected" ? "error" : "idle"}>{d.status}</Badge></div>
          {d.kind === "signal_card" ? (svgs[d.id] && <img src={svgs[d.id]} alt="signal card" className="w-full max-w-sm rounded-xl" />)
            : <pre className="text-sm whitespace-pre-wrap font-sans text-zinc-200" dir="auto">{d.body}</pre>}
          {d.kind !== "signal_card" && <button className="btn-ghost text-xs" onClick={() => navigator.clipboard?.writeText(d.body)}>{t("copy")}</button>}
        </div>
      ))}
    </div>
  );
}

function Activity() {
  const dec = useFetch<any[]>("/api/hq/decisions?limit=60");
  const inc = useFetch<any[]>("/api/hq/incidents");
  return (
    <div className="grid lg:grid-cols-2 gap-4">
      <div className="card card-pad">
        <div className="label mb-2">Signals & NO TRADE decisions</div>
        <ul className="space-y-1.5 text-xs max-h-[60vh] overflow-y-auto">
          {(dec.data ?? []).map((d) => (
            <li key={d.id} className="border-b border-graphite-700 pb-1">
              <span className={d.decision === "SIGNAL" ? "text-up font-bold" : "text-zinc-300"}>{d.decision}</span> {d.symbol} <span className="text-zinc-500">· {d.stage} · {dt(d.created_at)}</span>
              {d.audit_result && <Badge tone={d.audit_result === "avoided_loss" ? "ok" : "idle"}>{d.audit_result}</Badge>}
              <div className="text-zinc-500 truncate" dir="ltr">{(d.reasons ?? []).slice(0, 2).join(" · ")}</div>
            </li>
          ))}
        </ul>
      </div>
      <div className="card card-pad">
        <div className="label mb-2">Incidents (What broke)</div>
        {(inc.data ?? []).map((i) => (
          <div key={i.id} className="text-sm border-b border-graphite-700 py-2">
            <div className="flex justify-between gap-2"><b>{i.title}</b><Badge tone={i.status === "resolved" ? "ok" : "error"}>{i.status}</Badge></div>
            <div className="text-xs text-zinc-400 mt-1" dir="auto">{i.diagnosis}</div>
            {i.pr_url && <a className="text-xs text-accent underline" href={i.pr_url} target="_blank" rel="noreferrer">{i.branch} →</a>}
          </div>
        ))}
        {inc.data && !inc.data.length && <p className="text-xs text-zinc-500">—</p>}
      </div>
    </div>
  );
}
