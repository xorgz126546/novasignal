import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "en" | "ar";

const D: Record<string, { en: string; ar: string }> = {
  overview: { en: "Overview", ar: "نظرة عامة" },
  signals: { en: "Live Signals", ar: "الإشارات" },
  journal: { en: "Trade Journal", ar: "سجل الصفقات" },
  agents: { en: "Agent Monitor", ar: "مراقبة الوكلاء" },
  analytics: { en: "Analytics", ar: "التحليلات" },
  validation: { en: "30-Day Validation", ar: "اختبار 30 يومًا" },
  settings: { en: "Settings", ar: "الإعدادات" },
  admin: { en: "Admin", ar: "الإدارة" },
  hq: { en: "CRYPTONEX HQ", ar: "مقر CRYPTONEX" },
  signout: { en: "Sign out", ar: "تسجيل الخروج" },
  live: { en: "LIVE", ar: "مباشر" },
  offline: { en: "OFFLINE", ar: "غير متصل" },
  office: { en: "Office", ar: "المكتب" },
  needs_you: { en: "Needs you", ar: "ينتظر قرارك" },
  reports: { en: "Reports", ar: "التقارير" },
  knowledge: { en: "Knowledge", ar: "المعرفة" },
  drafts: { en: "Drafts", ar: "المسودات" },
  activity: { en: "Activity", ar: "النشاط" },
  chat_title: { en: "Talk to Maya (Manager)", ar: "تحدث مع مايا (المديرة)" },
  chat_ph: { en: "Ask Maya… e.g. scan, brief, learn about RSI", ar: "اكتب لمايا… مثال: افحص السوق، الموجز، تعلم عن RSI" },
  send: { en: "Send", ar: "إرسال" },
  run_cycle: { en: "Run trading cycle", ar: "تشغيل دورة التداول" },
  best_pick: { en: "⭐ Best pick today", ar: "⭐ أفضل توصية اليوم" },
  tournament: { en: "Strategy tournament", ar: "بطولة الاستراتيجيات" },
  leaderboard: { en: "Strategy leaderboard (real backtests)", ar: "ترتيب الاستراتيجيات (اختبار تاريخي حقيقي)" },
  run_audit: { en: "Run auditor", ar: "تشغيل المدقق" },
  run_growth: { en: "Trends + drafts", ar: "ترندات + مسودات" },
  make_brief: { en: "Write brief now", ar: "اكتب الموجز الآن" },
  inject: { en: "Inject test failure", ar: "حقن عطل تجريبي" },
  approve: { en: "Approve", ar: "موافقة" },
  reject: { en: "Reject", ar: "رفض" },
  rollback: { en: "Rollback", ar: "تراجع" },
  your_part: { en: "Your part today", ar: "دورك اليوم" },
  versions: { en: "Live versions", ar: "النسخ المفعّلة" },
  nothing_pending: { en: "Nothing is waiting for you.", ar: "لا شيء ينتظر قرارك." },
  level: { en: "Level", ar: "المستوى" },
  tasks: { en: "Recent tasks", ar: "آخر المهام" },
  logs: { en: "Logs", ar: "السجل" },
  skills: { en: "Skills file", ar: "ملف المهارات" },
  latest_report: { en: "Latest report", ar: "آخر تقرير" },
  tools: { en: "Tools", ar: "الأدوات" },
  learn: { en: "Learn about…", ar: "تعلّم عن…" },
  download: { en: "Download .md", ar: "تحميل .md" },
  copy: { en: "Copy", ar: "نسخ" },
  budget: { en: "AI budget today", ar: "ميزانية الذكاء اليوم" },
  disclaimer: {
    en: "Recommendations only — not financial advice. No profit is guaranteed. Binance Spot only; you decide and execute every trade yourself.",
    ar: "توصيات فقط وليست نصيحة مالية. لا يوجد ربح مضمون. Binance Spot فقط، وأنت من يقرر وينفّذ كل صفقة بنفسك.",
  },
  working: { en: "working", ar: "يعمل" },
  idle: { en: "idle", ar: "متاح" },
  blocked: { en: "blocked", ar: "متوقف" },
  hq_off: {
    en: "HQ_ENABLED is off: agents only work when you ask. Turn it on in Render → Environment for the schedule.",
    ar: "HQ_ENABLED غير مفعّل: الوكلاء يعملون فقط عند طلبك. فعّله من Render ← Environment لتشغيل الجدول التلقائي.",
  },
};

type Ctx = { lang: Lang; setLang: (l: Lang) => void; t: (k: string) => string; dir: "ltr" | "rtl" };
const LangCtx = createContext<Ctx>({ lang: "en", setLang: () => {}, t: (k) => k, dir: "ltr" });

function initial(): Lang {
  try {
    const s = localStorage.getItem("cnx_lang");
    if (s === "ar" || s === "en") return s;
  } catch {
    /* storage unavailable */
  }
  return navigator.language?.startsWith("ar") ? "ar" : "en";
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initial);
  const dir = lang === "ar" ? "rtl" : "ltr";
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = dir;
  }, [lang, dir]);
  const setLang = (l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem("cnx_lang", l);
    } catch {
      /* ignore */
    }
  };
  const t = (k: string) => D[k]?.[lang] ?? k;
  return <LangCtx.Provider value={{ lang, setLang, t, dir }}>{children}</LangCtx.Provider>;
}

export const useLang = () => useContext(LangCtx);

export function LangToggle({ className = "" }: { className?: string }) {
  const { lang, setLang } = useLang();
  return (
    <button aria-label="Language" className={`btn-ghost px-3 py-1.5 text-xs ${className}`} onClick={() => setLang(lang === "ar" ? "en" : "ar")}>
      {lang === "ar" ? "English" : "العربية"}
    </button>
  );
}
