// Chart conventions (dataviz checks): single-series charts have no legend box (title names them); recessive grid;
// 2px lines; straight segments (no smoothing that would invent values between trades); polarity bars are double-encoded by position vs the zero baseline + signed labels (green/red alone is
// only ΔE 7.4 for deuteranopia); brand red is never used as a data colour next to loss red (ΔE 3.5); text uses ink tokens.
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { price } from "../lib/format";

const GRID = "#26262d";
const AXIS = { stroke: "#55555f", fontSize: 11, tickLine: false, axisLine: false } as const;
const UP = "#22c55e";
const DOWN = "#ef4444";
const INK = "#d4d4d8";
const MUTED = "#a1a1aa";

const tooltipStyle = {
  contentStyle: { background: "#0d0d0f", border: "1px solid #34343d", borderRadius: 12, fontSize: 12, color: INK },
  labelStyle: { color: MUTED },
  itemStyle: { color: INK },
  cursor: { stroke: "#55555f", strokeWidth: 1 },
};

export function EquityChart({ data }: { data: { t: string; equity: number }[] }) {
  const pts = [{ t: "start", equity: 100 }, ...data];
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={pts} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <defs>
          <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={INK} stopOpacity={0.18} />
            <stop offset="100%" stopColor={INK} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" {...AXIS} tickFormatter={(t) => (t === "start" ? "" : new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" }))} minTickGap={32} />
        <YAxis {...AXIS} domain={["auto", "auto"]} width={48} tickFormatter={(v) => v.toFixed(0)} />
        <ReferenceLine y={100} stroke="#55555f" strokeDasharray="4 4" />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [`${v.toFixed(2)} (start = 100)`, "Equity index"]}
          labelFormatter={(t) => (t === "start" ? "Start" : new Date(t).toLocaleString())} />
        <Area type="linear" dataKey="equity" stroke={INK} strokeWidth={2} fill="url(#eq)" dot={pts.length <= 40 ? { r: 3, fill: INK, stroke: "#141417", strokeWidth: 2 } : false} activeDot={{ r: 4, stroke: "#141417", strokeWidth: 2 }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function PnlBars({ data, valueKey = "sum_return_pct" }: { data: any[]; valueKey?: string }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} barCategoryGap={2}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="period" {...AXIS} minTickGap={24} />
        <YAxis {...AXIS} width={48} tickFormatter={(v) => `${v}%`} />
        <ReferenceLine y={0} stroke="#55555f" />
        <Tooltip {...tooltipStyle} cursor={{ fill: "#ffffff08" }}
          formatter={(v: number, _n, p: any) => [`${v > 0 ? "+" : ""}${v.toFixed(2)}% · ${p.payload.trades} trade(s)`, "Net return"]} />
        <Bar dataKey={valueKey} maxBarSize={28} radius={[4, 4, 4, 4]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d[valueKey] >= 0 ? UP : DOWN} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

type Candle = { t: number; o: number; h: number; l: number; c: number };

export function PriceLevelsChart({ candles, entry, tp, sl, createdAt }: { candles: Candle[]; entry: number; tp: number; sl: number; createdAt: string }) {
  const created = new Date(createdAt).getTime();
  const data = candles.map((c) => ({ t: c.t, c: c.c, h: c.h, l: c.l }));
  const lo = Math.min(sl, ...data.map((d) => d.l));
  const hi = Math.max(tp, ...data.map((d) => d.h));
  const pad = (hi - lo) * 0.05;
  const label = (text: string) => ({ value: text, position: "insideTopRight" as const, fill: INK, fontSize: 11 });
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data} margin={{ top: 12, right: 12, left: 4, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time" {...AXIS}
          tickFormatter={(t) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" })} minTickGap={40} />
        <YAxis {...AXIS} domain={[lo - pad, hi + pad]} width={72} tickFormatter={(v) => price(v)} />
        <Tooltip {...tooltipStyle} labelFormatter={(t) => new Date(t).toLocaleString()} formatter={(v: number) => [price(v), "Close"]} />
        <ReferenceLine y={tp} stroke={UP} strokeWidth={1.5} strokeDasharray="6 4" label={label(`TP ${price(tp)}`)} />
        <ReferenceLine y={entry} stroke={MUTED} strokeWidth={1.5} strokeDasharray="2 3" label={label(`Entry ${price(entry)}`)} />
        <ReferenceLine y={sl} stroke={DOWN} strokeWidth={1.5} strokeDasharray="6 4" label={label(`SL ${price(sl)}`)} />
        <ReferenceLine x={created} stroke="#55555f" label={{ value: "signal", position: "insideTopRight", fill: MUTED, fontSize: 10, offset: 6 }} />
        <Line type="linear" dataKey="c" stroke={INK} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "#141417", strokeWidth: 2 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function FunnelBars({ items }: { items: { label: string; value: number }[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="space-y-3">
      {items.map((i) => (
        <div key={i.label}>
          <div className="flex justify-between text-xs mb-1"><span className="text-zinc-400">{i.label}</span><span className="num text-zinc-200">{i.value}</span></div>
          <div className="h-2 rounded-full bg-graphite-600 overflow-hidden"><div className="h-full rounded-full bg-zinc-300" style={{ width: `${(i.value / max) * 100}%` }} /></div>
        </div>
      ))}
    </div>
  );
}
