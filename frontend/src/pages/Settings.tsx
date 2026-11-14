import { useEffect, useState } from "react";
import { Badge, ErrorBox, Loading, PageHeader, Section } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch } from "../lib/useFetch";

const PREF_LABELS: Record<string, string> = {
  new_signal: "New signals", target_hit: "Target hit", stop_loss: "Stop loss hit", expired: "Signal expired",
  daily_summary: "Daily performance summary", system_alerts: "System alerts (admins)",
};

function NumField({ label, value, onChange, step = 0.1, disabled }: { label: string; value: number; onChange: (v: number) => void; step?: number; disabled?: boolean }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input type="number" step={step} className="input mt-1 num" value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

export default function Settings() {
  const { user, refresh } = useAuth();
  const { data, error, loading, reload } = useFetch<any>("/api/settings");
  const [cfg, setCfg] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });

  useEffect(() => { if (data) setCfg(structuredClone(data.trading)); }, [data]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const d = data!;
  const editable = d.editable;
  const run = async (fn: () => Promise<any>, ok: string) => {
    setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); reload(); refresh(); } catch (e: any) { setErr(e.message); }
  };
  const r = cfg?.risk, ind = cfg?.indicators;
  const setR = (k: string, v: number) => setCfg({ ...cfg, risk: { ...cfg.risk, [k]: v } });
  const setI = (k: string, v: number) => setCfg({ ...cfg, indicators: { ...cfg.indicators, [k]: v } });

  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle={editable ? "Trading settings apply to the whole engine and are audit-logged." : "Trading settings are managed by the administrator."} />
      {msg && <div className="rounded-xl border border-up/40 bg-up/10 text-up text-sm px-4 py-3">{msg}</div>}
      {err && <ErrorBox error={err} />}

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Binance market data">
          <p className="text-sm text-zinc-300">{d.binance.mode}</p>
          <p className="text-xs text-zinc-500 mt-2 num">{d.binance.base_url}</p>
          <p className="text-xs text-zinc-500 mt-3">No API key is needed or stored. CRYPTONEX AI never places orders and never requests withdrawal permission.</p>
        </Section>
        <Section title="Telegram" right={d.telegram.linked ? <Badge tone="ok">LINKED</Badge> : <Badge tone="idle">NOT LINKED</Badge>}>
          {!d.telegram.bot_configured ? (
            <p className="text-sm text-zinc-400">The bot is not configured on the server yet (TELEGRAM_BOT_TOKEN). See docs/DEPLOYMENT.md.</p>
          ) : d.telegram.linked ? (
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={() => run(() => api("/api/settings/telegram/link", { method: "DELETE" }), "Telegram unlinked")}>Unlink</button>
              {user?.role === "admin" && <button className="btn-ghost" onClick={() => run(() => api("/api/telegram/test", { method: "POST" }), "Test message sent")}>Send test message</button>}
            </div>
          ) : (
            <div className="space-y-3">
              <button className="btn-primary" onClick={async () => { const x = await api<any>("/api/settings/telegram/link", { method: "POST" }); setLink(x.code); }}>Generate link code</button>
              {link && <p className="text-sm">Send <code className="num bg-graphite-900 px-2 py-1 rounded">/start {link}</code> to the CRYPTONEX AI bot.</p>}
            </div>
          )}
        </Section>
      </div>

      <Section title="Notification preferences">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Object.entries(d.notification_prefs).map(([k, v]) => (
            <label key={k} className="flex items-center justify-between rounded-xl border border-graphite-700 px-3 py-2.5 text-sm cursor-pointer">
              <span>{PREF_LABELS[k] ?? k}</span>
              <input type="checkbox" checked={v as boolean} className="accent-[#ff2a3d] h-4 w-4"
                onChange={(e) => run(() => api("/api/settings/notifications", { method: "PUT", json: { prefs: { [k]: e.target.checked } } }), "Preferences saved")} />
            </label>
          ))}
        </div>
      </Section>

      {cfg && (
        <div className="grid lg:grid-cols-2 gap-4">
          <Section title="Risk settings">
            <div className="grid grid-cols-2 gap-3">
              <NumField label="Max signals / day" value={r.max_signals_per_day} step={1} onChange={(v) => setR("max_signals_per_day", v)} disabled={!editable} />
              <NumField label="Min risk/reward (net)" value={r.min_risk_reward} onChange={(v) => setR("min_risk_reward", v)} disabled={!editable} />
              <NumField label="Target move min %" value={r.target_move_min_pct} onChange={(v) => setR("target_move_min_pct", v)} disabled={!editable} />
              <NumField label="Target move max %" value={r.target_move_max_pct} onChange={(v) => setR("target_move_max_pct", v)} disabled={!editable} />
              <NumField label="Min stop distance %" value={r.min_stop_distance_pct} onChange={(v) => setR("min_stop_distance_pct", v)} disabled={!editable} />
              <NumField label="Max stop distance %" value={r.max_stop_distance_pct} onChange={(v) => setR("max_stop_distance_pct", v)} disabled={!editable} />
              <NumField label="Risk per trade %" value={r.risk_per_trade_pct} onChange={(v) => setR("risk_per_trade_pct", v)} disabled={!editable} />
              <NumField label="Max open user trades" value={r.max_open_user_trades} step={1} onChange={(v) => setR("max_open_user_trades", v)} disabled={!editable} />
              <NumField label="Fee per side %" value={r.fee_rate_pct} step={0.01} onChange={(v) => setR("fee_rate_pct", v)} disabled={!editable} />
              <NumField label="Slippage per side %" value={r.slippage_pct} step={0.01} onChange={(v) => setR("slippage_pct", v)} disabled={!editable} />
              <NumField label="Min 24h volume (USDT)" value={r.min_quote_volume_usdt} step={1000000} onChange={(v) => setR("min_quote_volume_usdt", v)} disabled={!editable} />
              <NumField label="Min confidence" value={r.min_confidence} step={1} onChange={(v) => setR("min_confidence", v)} disabled={!editable} />
            </div>
          </Section>
          <Section title="Indicator settings">
            <div className="grid grid-cols-2 gap-3">
              <NumField label="RSI period" value={ind.rsi_period} step={1} onChange={(v) => setI("rsi_period", v)} disabled={!editable} />
              <NumField label="ADX min (trend)" value={ind.adx_min} step={1} onChange={(v) => setI("adx_min", v)} disabled={!editable} />
              <NumField label="RSI min" value={ind.rsi_min} step={1} onChange={(v) => setI("rsi_min", v)} disabled={!editable} />
              <NumField label="RSI max" value={ind.rsi_max} step={1} onChange={(v) => setI("rsi_max", v)} disabled={!editable} />
              <NumField label="MACD fast" value={ind.macd_fast} step={1} onChange={(v) => setI("macd_fast", v)} disabled={!editable} />
              <NumField label="MACD slow" value={ind.macd_slow} step={1} onChange={(v) => setI("macd_slow", v)} disabled={!editable} />
              <NumField label="Bollinger period" value={ind.bb_period} step={1} onChange={(v) => setI("bb_period", v)} disabled={!editable} />
              <NumField label="ATR period" value={ind.atr_period} step={1} onChange={(v) => setI("atr_period", v)} disabled={!editable} />
            </div>
            {editable && <button className="btn-primary mt-5" onClick={() => run(() => api("/api/settings/trading", { method: "PUT", json: cfg }), "Trading settings saved")}>Save trading settings</button>}
          </Section>
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Profile">
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-zinc-500">Email</dt><dd>{d.profile.email}</dd>
            <dt className="text-zinc-500">Role</dt><dd>{d.profile.role}</dd>
            <dt className="text-zinc-500">Subscription</dt><dd>{user?.subscription_status}{!d.billing_enabled && " (billing not active — private testing)"}</dd>
          </dl>
        </Section>
        <Section title="Security">
          <div className="grid grid-cols-2 gap-3">
            <label><span className="label">Current password</span><input type="password" className="input mt-1" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></label>
            <label><span className="label">New password</span><input type="password" className="input mt-1" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></label>
          </div>
          <button className="btn-ghost mt-4" disabled={pw.new_password.length < 10} onClick={() => run(() => api("/api/settings/password", { method: "PUT", json: pw }), "Password changed")}>Change password</button>
        </Section>
      </div>
    </div>
  );
}
