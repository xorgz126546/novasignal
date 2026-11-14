import { useEffect, useRef, useState } from "react";
import { getToken } from "../lib/api";

export type HQEvent = { id: number; at: string; type: string; agent_id: string | null; data: Record<string, any> };

/** Live HQ events over Server-Sent Events (auto-reconnect handled by EventSource). */
export function useHQStream(onEvent: (e: HQEvent) => void) {
  const [live, setLive] = useState(false);
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const base = (import.meta as any).env?.VITE_API_URL ?? "";
    const es = new EventSource(`${base}/api/hq/stream?token=${encodeURIComponent(token)}`);
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("hq", (m) => {
      try {
        cb.current(JSON.parse((m as MessageEvent).data));
      } catch {
        /* ignore malformed */
      }
    });
    return () => es.close();
  }, []);
  return live;
}
