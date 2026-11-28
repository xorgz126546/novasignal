// Minimal, safe markdown renderer (headings, lists, checkboxes, bold, italics). No HTML injection possible.
import { Fragment, type ReactNode } from "react";

function inline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> :
    p.startsWith("_") && p.endsWith("_") && p.length > 2 ? <i key={i} className="text-zinc-400">{p.slice(1, -1)}</i> :
    <Fragment key={i}>{p}</Fragment>);
}

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-1 text-sm leading-relaxed" dir="auto">
      {text.split("\n").map((l, i) => {
        if (l.startsWith("# ")) return <h3 key={i} className="text-base font-bold mt-2">{inline(l.slice(2))}</h3>;
        if (l.startsWith("## ")) return <h4 key={i} className="font-semibold mt-3 text-zinc-200">{inline(l.slice(3))}</h4>;
        if (l.startsWith("- [ ] ")) return <div key={i} className="flex gap-2"><span>☐</span><span>{inline(l.slice(6))}</span></div>;
        if (l.startsWith("- ")) return <div key={i} className="flex gap-2"><span className="text-zinc-500">•</span><span>{inline(l.slice(2))}</span></div>;
        if (!l.trim()) return <div key={i} className="h-1" />;
        return <p key={i}>{inline(l)}</p>;
      })}
    </div>
  );
}
