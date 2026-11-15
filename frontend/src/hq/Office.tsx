// Live pixel-art office. The canvas draws floor/rooms/desks/lamps/sprites (all code-generated, no assets);
// an HTML overlay draws name tags, level badges and speech bubbles (crisp on phones, RTL-safe).
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

export type OfficeAgent = {
  id: string;
  name: { en: string; ar: string };
  room: string;
  color: string;
  level: number;
  status: string;
  current_task: string;
};

const T = 8; // px per tile
const W = 48;
const H = 30;
type Rect = { x: number; y: number; w: number; h: number; label: { en: string; ar: string }; floor: string };
export const ROOMS: Record<string, Rect> = {
  scanner_floor: { x: 0, y: 0, w: 16, h: 13, label: { en: "Scanner floor", ar: "قاعة المسح" }, floor: "#10161d" },
  strategy_lab: { x: 16, y: 0, w: 16, h: 13, label: { en: "Strategy Lab", ar: "مختبر الاستراتيجيات" }, floor: "#15111d" },
  risk_office: { x: 32, y: 0, w: 16, h: 13, label: { en: "Risk Office", ar: "مكتب المخاطر" }, floor: "#1a1012" },
  backtest_lab: { x: 0, y: 16, w: 10, h: 14, label: { en: "Backtest Lab", ar: "مختبر الاختبار" }, floor: "#0f1a18" },
  library: { x: 10, y: 16, w: 10, h: 14, label: { en: "Library", ar: "المكتبة" }, floor: "#16140e" },
  engineering: { x: 20, y: 16, w: 9, h: 14, label: { en: "Engineering", ar: "الهندسة" }, floor: "#0f141c" },
  content_studio: { x: 29, y: 16, w: 9, h: 14, label: { en: "Content Studio", ar: "استوديو المحتوى" }, floor: "#1a1018" },
  manager_office: { x: 38, y: 16, w: 10, h: 14, label: { en: "Manager's office", ar: "مكتب المديرة" }, floor: "#1c1414" },
};
const HANDOVER = { x: (38 + 3) * T, y: (16 + 9) * T }; // spot in front of the Manager's desk

type Sprite = { home: { x: number; y: number }; x: number; y: number; path: { x: number; y: number; wait?: number }[]; walkT: number; carrying: boolean };

function homes(agents: OfficeAgent[]) {
  const byRoom: Record<string, OfficeAgent[]> = {};
  agents.forEach((a) => (byRoom[a.room] ??= []).push(a));
  const out: Record<string, { x: number; y: number }> = {};
  for (const [room, list] of Object.entries(byRoom)) {
    const r = ROOMS[room] ?? ROOMS.scanner_floor;
    const cols = Math.max(1, Math.floor((r.w - 2) / 5));
    list.forEach((a, i) => {
      const cx = r.x + 2 + (i % cols) * 5 + (a.id === "manager" ? 3 : 0);
      const cy = r.y + 4 + Math.floor(i / cols) * 5;
      out[a.id] = { x: cx * T + 6, y: cy * T + 10 };
    });
  }
  return out;
}

function drawSprite(g: CanvasRenderingContext2D, x: number, y: number, color: string, frame: number, working: boolean, carrying: boolean) {
  const px = (dx: number, dy: number, w: number, h: number, c: string) => {
    g.fillStyle = c;
    g.fillRect(Math.round(x + dx), Math.round(y + dy), w, h);
  };
  const bob = working ? (frame % 2) * -1 : 0;
  px(-3, 5, 7, 2, "#00000066"); // shadow
  px(-2, -9 + bob, 5, 4, "#f2c9a0"); // head
  px(-2, -10 + bob, 5, 2, "#3b2a20"); // hair
  px(-3, -5 + bob, 7, 6, color); // body
  px(-4, -4 + bob, 1, 4, color);
  px(4, -4 + bob, 1, 4, color);
  const leg = frame % 2 === 0 ? 0 : 1;
  px(-2, 1, 2, 4 - leg, "#1f2937");
  px(1, 1, 2, 3 + leg, "#1f2937");
  if (carrying) px(4, -6 + bob, 4, 5, "#f8fafc"); // report sheet
}

export type OfficeHandle = { handover: (id: string) => void };

type Props = { agents: OfficeAgent[]; lang: "en" | "ar"; onSelect: (id: string) => void; selected?: string | null };

export default forwardRef<OfficeHandle, Props>(function Office({ agents, lang, onSelect, selected }, ref) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const tags = useRef<Record<string, HTMLButtonElement | null>>({});
  const sprites = useRef<Record<string, Sprite>>({});
  const agentsRef = useRef(agents);
  agentsRef.current = agents;

  useEffect(() => {
    const hs = homes(agents);
    for (const a of agents) {
      const h = hs[a.id];
      const s = sprites.current[a.id];
      if (!s) sprites.current[a.id] = { home: h, x: h.x, y: h.y, path: [], walkT: 0, carrying: false };
      else s.home = h;
    }
  }, [agents]);

  useImperativeHandle(ref, () => ({
    handover(id: string) {
      const s = sprites.current[id];
      if (!s || id === "manager" || s.path.length) return;
      const corridorY = 14.5 * T;
      s.carrying = true;
      s.path = [
        { x: s.home.x, y: corridorY }, { x: HANDOVER.x, y: corridorY }, { x: HANDOVER.x, y: HANDOVER.y, wait: 70 },
        { x: HANDOVER.x, y: corridorY }, { x: s.home.x, y: corridorY }, { x: s.home.x, y: s.home.y },
      ];
    },
  }));

  useEffect(() => {
    const c = canvas.current!;
    const g = c.getContext("2d")!;
    let raf = 0;
    let tick = 0;
    const loop = () => {
      tick++;
      const frame = Math.floor(tick / 10);
      g.fillStyle = "#08080a";
      g.fillRect(0, 0, W * T, H * T);
      // corridor
      g.fillStyle = "#0d0d10";
      g.fillRect(0, 13 * T, W * T, 3 * T);
      for (let x = 0; x < W; x += 2) {
        g.fillStyle = "#16161b";
        g.fillRect(x * T + 3, 14 * T + 7, 4, 1);
      }
      // rooms
      for (const r of Object.values(ROOMS)) {
        g.fillStyle = r.floor;
        g.fillRect(r.x * T, r.y * T, r.w * T, r.h * T);
        g.fillStyle = "#ffffff06";
        for (let yy = r.y; yy < r.y + r.h; yy++) for (let xx = r.x; xx < r.x + r.w; xx++) if ((xx + yy) % 2 === 0) g.fillRect(xx * T, yy * T, T, T);
        g.strokeStyle = "#2a2a33";
        g.lineWidth = 2;
        g.strokeRect(r.x * T + 1, r.y * T + 1, r.w * T - 2, r.h * T - 2);
        g.fillStyle = "#0d0d10"; // door to corridor
        const doorY = r.y === 0 ? r.y * T + r.h * T - 2 : r.y * T;
        g.fillRect(r.x * T + (r.w * T) / 2 - 6, doorY - 1, 12, 4);
      }
      // library shelves + backtest screens (decor)
      for (let i = 0; i < 3; i++) {
        g.fillStyle = "#3b2f1e";
        g.fillRect((10 + 1) * T, (16 + 2 + i * 3) * T, 8 * T, 6);
        g.fillStyle = ["#7c3aed", "#0ea5e9", "#f59e0b"][i];
        for (let b = 0; b < 12; b++) g.fillRect((11) * T + b * 5 + 2, (16 + 2 + i * 3) * T - 5, 3, 5);
      }
      // desks, lamps, sprites
      const list = agentsRef.current;
      for (const a of list) {
        const s = sprites.current[a.id];
        if (!s) continue;
        const working = a.status === "working";
        const dx = s.home.x - 9;
        const dy = s.home.y - 16;
        g.fillStyle = a.id === "manager" ? "#5b3a29" : "#2b2b33";
        g.fillRect(dx, dy, 18, 7); // desk
        g.fillStyle = working ? "#1e293b" : "#111827";
        g.fillRect(dx + 3, dy - 6, 8, 6); // monitor
        if (working) {
          g.fillStyle = a.color + "aa";
          g.fillRect(dx + 4, dy - 5, 6, 4);
        }
        g.fillStyle = "#6b7280";
        g.fillRect(dx + 14, dy - 5, 1, 5); // lamp pole
        g.fillStyle = working ? "#fde047" : "#3f3f46";
        g.fillRect(dx + 12, dy - 7, 5, 2); // lamp head
        if (working) {
          const grd = g.createRadialGradient(dx + 14, dy - 4, 1, dx + 14, dy - 4, 14);
          grd.addColorStop(0, "#fde04755");
          grd.addColorStop(1, "#fde04700");
          g.fillStyle = grd;
          g.fillRect(dx, dy - 18, 30, 30);
        }
        if (a.status === "blocked") {
          g.fillStyle = "#ef4444";
          g.fillRect(dx + 17, dy - 9, 2, 5);
          g.fillRect(dx + 17, dy - 3, 2, 2);
        }
      }
      for (const a of list) {
        const s = sprites.current[a.id];
        if (!s) continue;
        let moving = false;
        if (s.path.length) {
          const p = s.path[0];
          const vx = p.x - s.x;
          const vy = p.y - s.y;
          const d = Math.hypot(vx, vy);
          if (d < 1.2) {
            if (p.wait && p.wait > 0) {
              p.wait--;
              if (p.wait === 1) s.carrying = false;
            } else s.path.shift();
          } else {
            s.x += (vx / d) * 1.2;
            s.y += (vy / d) * 1.2;
            moving = true;
          }
        }
        drawSprite(g, s.x, s.y, a.color, moving ? frame : a.status === "working" ? frame : 0, a.status === "working" && !moving, s.carrying);
        const tag = tags.current[a.id];
        if (tag) {
          tag.style.left = `${(s.x / (W * T)) * 100}%`;
          tag.style.top = `${((s.y - 12) / (H * T)) * 100}%`;
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="relative w-full select-none" dir="ltr" style={{ aspectRatio: `${W} / ${H}` }}>
      <canvas ref={canvas} width={W * T} height={H * T} className="absolute inset-0 w-full h-full rounded-xl" style={{ imageRendering: "pixelated" }} />
      {Object.entries(ROOMS).map(([k, r]) => (
        <div key={k} className="absolute text-[8px] sm:text-[11px] font-semibold text-zinc-500 pointer-events-none px-1 whitespace-nowrap overflow-hidden text-ellipsis"
          style={{ left: `${(r.x / W) * 100}%`, top: `${(r.y / H) * 100}%`, width: `${(r.w / W) * 100}%` }} dir={lang === "ar" ? "rtl" : "ltr"}>
          {r.label[lang]}
        </div>
      ))}
      {agents.map((a) => (
        <button key={a.id} ref={(el) => (tags.current[a.id] = el)} onClick={() => onSelect(a.id)}
          className="absolute -translate-x-1/2 -translate-y-full flex flex-col items-center group" style={{ left: 0, top: 0 }}>
          {a.status === "working" && a.current_task && (
            <span className="mb-0.5 max-w-[110px] sm:max-w-[170px] truncate rounded-md bg-white text-black text-[8px] sm:text-[10px] px-1.5 py-0.5 shadow"
              dir="auto">
              {a.current_task}
            </span>
          )}
          <span className={`whitespace-nowrap rounded px-1 text-[8px] sm:text-[10px] font-semibold ${selected === a.id ? "bg-accent text-white" : "bg-black/70 text-zinc-100"}`}>
            {(a.name[lang] || a.name.en).split(" — ")[0]} <span className="text-amber-300">Lv{a.level}</span>
          </span>
        </button>
      ))}
    </div>
  );
});
