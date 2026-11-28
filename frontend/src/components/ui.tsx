import type { ReactNode } from "react";

export function Logo({ small = false }: { small?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 select-none">
      <svg className="shrink-0" width={small ? 26 : 32} height={small ? 26 : 32} viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#141417" stroke="#26262d" />
        <path d="M7 22 L13 14 L18 18 L25 9" stroke="#ff2a3d" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="25" cy="9" r="2.5" fill="#ff2a3d" />
      </svg>
      <div className="leading-none">
        <div className={`font-extrabold tracking-[0.18em] ${small ? "text-sm" : "text-base"}`}>
          CRYPTONEX <span className="text-accent">AI</span>
        </div>
        {!small && <div className="text-[9px] tracking-[0.2em] text-zinc-500 mt-1 whitespace-nowrap">INTELLIGENT CRYPTO SIGNALS</div>}
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3 mb-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-zinc-400 mt-1 max-w-2xl">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2 flex-wrap">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="card card-pad">
      <div className="label">{label}</div>
      <div className={`num text-2xl font-semibold mt-2 ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-zinc-500 mt-1">{sub}</div>}
    </div>
  );
}

export function Section({ title, children, right, className = "" }: { title: string; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`card card-pad ${className}`}>
      <div className="flex items-center justify-between mb-4 gap-2">
        <h2 className="text-sm font-semibold tracking-wide">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

const STATUS_STYLE: Record<string, string> = {
  ACTIVE: "bg-accent/15 text-accent border-accent/40",
  APPROVED: "bg-accent/10 text-accent border-accent/30",
  ANALYZING: "bg-zinc-500/10 text-zinc-300 border-zinc-500/30",
  USER_ENTERED: "bg-sky-500/10 text-sky-300 border-sky-500/30",
  USER_SKIPPED: "bg-zinc-500/10 text-zinc-400 border-zinc-500/30",
  TARGET_HIT: "bg-up/10 text-up border-up/30",
  STOP_LOSS_HIT: "bg-down/10 text-down border-down/30",
  EXPIRED: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  CANCELLED: "bg-zinc-600/10 text-zinc-500 border-zinc-600/30",
  CLOSED_MANUALLY: "bg-violet-500/10 text-violet-300 border-violet-500/30",
  ok: "bg-up/10 text-up border-up/30",
  idle: "bg-zinc-500/10 text-zinc-400 border-zinc-500/30",
  degraded: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  error: "bg-down/10 text-down border-down/30",
  OPEN: "bg-sky-500/10 text-sky-300 border-sky-500/30",
  CLOSED: "bg-zinc-500/10 text-zinc-300 border-zinc-500/30",
  SKIPPED: "bg-zinc-500/10 text-zinc-400 border-zinc-500/30",
  running: "bg-accent/10 text-accent border-accent/30",
  completed: "bg-up/10 text-up border-up/30",
};

export function Badge({ children, tone }: { children: ReactNode; tone?: string }) {
  const key = tone ?? String(children);
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-semibold tracking-wide ${STATUS_STYLE[key] ?? "border-graphite-500 text-zinc-300"}`}>
      {String(children).replace(/_/g, " ")}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="text-center py-10 px-4">
      <div className="mx-auto mb-3 h-10 w-10 rounded-full border border-graphite-500 grid place-items-center text-zinc-500">∅</div>
      <div className="font-medium text-zinc-200">{title}</div>
      {children && <div className="text-sm text-zinc-500 mt-1 max-w-md mx-auto">{children}</div>}
    </div>
  );
}

export function Loading() {
  return (
    <div className="space-y-3 animate-pulse">
      <div className="h-6 w-48 rounded bg-graphite-700" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-24 rounded-2xl bg-graphite-800" />
        ))}
      </div>
    </div>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="rounded-xl border border-down/40 bg-down/10 text-down text-sm px-4 py-3">{error}</div>;
}

export function Bar({ value, max = 100, tone = "bg-accent" }: { value: number; max?: number; tone?: string }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-graphite-600 overflow-hidden">
      <div className={`h-full ${tone}`} style={{ width: `${Math.max(0, Math.min(100, (value / max) * 100))}%` }} />
    </div>
  );
}

export function RiskNote() {
  return (
    <p className="text-[11px] leading-relaxed text-zinc-500">
      Signals are analytical output, not financial advice. No profit, win rate or daily opportunity is guaranteed. Spot only — you
      decide and execute every trade manually on Binance. Crypto assets are volatile and you can lose money.
    </p>
  );
}
