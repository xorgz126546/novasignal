import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { LangToggle, useLang } from "../lib/i18n";
import { Logo } from "./ui";

const NAV = [
  { to: "/app", label: "overview", icon: "M3 12h4v8H3zM10 4h4v16h-4zM17 9h4v11h-4z", end: true },
  { to: "/app/signals", label: "signals", icon: "M3 17l6-6 4 4 8-8M15 7h6v6" },
  { to: "/app/journal", label: "journal", icon: "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5" },
  { to: "/app/agents", label: "agents", icon: "M12 3a4 4 0 110 8 4 4 0 010-8zM4 21a8 8 0 0116 0" },
  { to: "/app/analytics", label: "analytics", icon: "M4 19V5M4 19h16M8 15l3-4 3 2 5-6" },
  { to: "/app/validation", label: "validation", icon: "M5 4h14v16H5zM9 12l2 2 4-4" },
  { to: "/app/settings", label: "settings", icon: "M12 9a3 3 0 110 6 3 3 0 010-6zM4 12h2M18 12h2M12 4v2M12 18v2" },
];

function Icon({ d }: { d: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const { t } = useLang();
  const items = user?.role === "admin" ? [
    { to: "/app/hq", label: "hq", icon: "M3 21V8l9-5 9 5v13M9 21v-6h6v6M7 11h2M15 11h2" }, ...NAV,
    { to: "/app/admin", label: "admin", icon: "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" }] : NAV;

  const links = (
    <nav className="flex flex-col gap-1">
      {items.map((n) => (
        <NavLink key={n.to} to={n.to} end={(n as any).end} onClick={() => setOpen(false)}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${isActive ? "bg-graphite-700 text-white shadow-[inset_2px_0_0_#ff2a3d] rtl:shadow-[inset_-2px_0_0_#ff2a3d]" : "text-zinc-400 hover:text-zinc-100 hover:bg-graphite-800"}`}>
          <Icon d={n.icon} />
          {t(n.label)}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen lg:flex">
      <aside className="hidden lg:flex w-64 shrink-0 flex-col border-e border-graphite-700 bg-graphite-900/60 px-4 py-6 sticky top-0 h-screen">
        <div className="px-2 mb-8"><Logo /></div>
        {links}
        <div className="mt-auto pt-6 border-t border-graphite-700">
          <LangToggle className="mb-4 mx-2" />
          <div className="px-2 text-xs text-zinc-500 truncate">{user?.email}</div>
          <div className="px-2 mt-1 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-widest text-zinc-600">{user?.role}</span>
            <button className="text-xs text-zinc-400 hover:text-accent" onClick={() => { logout(); nav("/"); }}>{t("signout")}</button>
          </div>
        </div>
      </aside>

      <header className="lg:hidden sticky top-0 z-30 flex items-center justify-between px-4 py-3 border-b border-graphite-700 bg-ink/90 backdrop-blur">
        <Logo small />
        <div className="flex gap-2"><LangToggle /><button aria-label="Menu" className="btn-ghost px-3" onClick={() => setOpen(!open)}>☰</button></div>
      </header>
      {open && (
        <div className="lg:hidden fixed inset-0 z-20 bg-ink pt-20 px-4 pb-8 overflow-y-auto">
          {links}
          <button className="btn-ghost w-full mt-6" onClick={() => { logout(); nav("/"); }}>{t("signout")}</button>
        </div>
      )}

      <main className="flex-1 min-w-0 px-4 sm:px-6 lg:px-10 py-6 lg:py-8 max-w-[1400px]">
        <Outlet />
      </main>
    </div>
  );
}
