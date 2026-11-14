import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export function useFetch<T>(path: string | null, intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message || "Request failed");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(!!path);
    load();
    let t: any;
    if (intervalMs) t = setInterval(load, intervalMs);
    return () => {
      alive.current = false;
      if (t) clearInterval(t);
    };
  }, [load, intervalMs, path]);

  // Before the first response for a non-null path, report loading (avoids a render with data=null & loading=false).
  return { data, error, loading: loading || (!!path && data === null && error === null), reload: load };
}
