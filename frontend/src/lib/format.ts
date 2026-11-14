export function price(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "—";
  if (p >= 1000) return p.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  if (p >= 1) return p.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return Number(p.toPrecision(6)).toString();
}

export function pct(v: number | null | undefined, digits = 2, sign = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${sign && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function ratio(v: number | null | undefined | string): string {
  if (v === "inf") return "∞";
  if (v === null || v === undefined || typeof v === "string") return "—";
  return v.toFixed(2);
}

export function rate(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

export function dt(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(s: string | null | undefined): string {
  if (!s) return "never";
  const sec = (Date.now() - new Date(s).getTime()) / 1000;
  if (sec < 60) return `${Math.max(0, Math.round(sec))}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

export function until(s: string): string {
  const sec = (new Date(s).getTime() - Date.now()) / 1000;
  if (sec <= 0) return "passed";
  if (sec < 3600) return `in ${Math.round(sec / 60)}m`;
  return `in ${(sec / 3600).toFixed(1)}h`;
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-zinc-300";
  return v > 0 ? "text-up" : "text-down";
}
