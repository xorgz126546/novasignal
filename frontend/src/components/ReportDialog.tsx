import { useState } from "react";
import { api } from "../lib/api";
import { price } from "../lib/format";
import { ErrorBox } from "./ui";

type Props = { signal: { id: number; pair: string; entry_price: number; take_profit: number }; action: "ENTERED" | "SKIPPED" | "CLOSED"; onClose: () => void; onDone: () => void };

export default function ReportDialog({ signal, action, onClose, onDone }: Props) {
  const [priceV, setPrice] = useState(action === "ENTERED" ? String(signal.entry_price) : "");
  const [qty, setQty] = useState("");
  const [fees, setFees] = useState("");
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/signals/${signal.id}/report`, {
        method: "POST",
        json: {
          action,
          price: action === "SKIPPED" ? null : Number(priceV),
          quantity: qty ? Number(qty) : null,
          fees: fees ? Number(fees) : 0,
          notes,
        },
      });
      onDone();
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const title = { ENTERED: "Report entry", SKIPPED: "Skip signal", CLOSED: "Report exit" }[action];
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 px-4" role="dialog" aria-modal onClick={onClose}>
      <div className="card p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold">{title} · {signal.pair}</h3>
        <p className="text-sm text-zinc-400 mt-1">
          {action === "ENTERED" && `Signal entry was ${price(signal.entry_price)}. Enter the price you actually got on Binance.`}
          {action === "SKIPPED" && "Record that you did not take this trade. It stays in the theoretical and paper tracks only."}
          {action === "CLOSED" && "Enter the actual exit price and the total fees you paid (USDT)."}
        </p>
        <div className="space-y-3 mt-5">
          {err && <ErrorBox error={err} />}
          {action !== "SKIPPED" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2"><label className="label">{action === "ENTERED" ? "Actual entry price" : "Actual exit price"}</label>
                <input className="input mt-1 num" inputMode="decimal" value={priceV} onChange={(e) => setPrice(e.target.value)} /></div>
              <div><label className="label">Quantity (optional)</label><input className="input mt-1 num" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} /></div>
              <div><label className="label">Fees USDT</label><input className="input mt-1 num" inputMode="decimal" value={fees} onChange={(e) => setFees(e.target.value)} /></div>
            </div>
          )}
          <div><label className="label">Notes</label><textarea className="input mt-1" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
        </div>
        <div className="flex gap-2 justify-end mt-6">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || (action !== "SKIPPED" && !(Number(priceV) > 0))} onClick={submit}>{busy ? "Saving…" : "Confirm"}</button>
        </div>
      </div>
    </div>
  );
}
