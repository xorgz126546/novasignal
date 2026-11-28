import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";

export type User = {
  id: number;
  email: string;
  display_name: string;
  role: "user" | "admin";
  telegram_linked: boolean;
  has_access: boolean;
  subscription_status: string;
};

type AuthCtx = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string, accept: boolean) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>(null as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await api<User>("/api/auth/me"));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onLogout = () => setUser(null);
    window.addEventListener("cnx:logout", onLogout);
    return () => window.removeEventListener("cnx:logout", onLogout);
  }, [refresh]);

  const login = async (email: string, password: string) => {
    const r = await api<{ token: string; user: User }>("/api/auth/login", { method: "POST", json: { email, password } });
    setToken(r.token);
    setUser(r.user);
  };
  const register = async (email: string, password: string, display_name: string, accept: boolean) => {
    const r = await api<{ token: string; user: User }>("/api/auth/register", {
      method: "POST",
      json: { email, password, display_name, accept_risk_disclosure: accept },
    });
    setToken(r.token);
    setUser(r.user);
  };
  const logout = () => {
    setToken(null);
    setUser(null);
  };
  return <Ctx.Provider value={{ user, loading, login, register, logout, refresh }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
