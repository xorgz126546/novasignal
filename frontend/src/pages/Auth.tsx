import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ErrorBox, Logo, RiskNote } from "../components/ui";
import { useAuth } from "../lib/auth";

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen grid place-items-center px-4 py-10 bg-[radial-gradient(ellipse_at_top,#ff2a3d14,transparent_60%)]">
      <div className="w-full max-w-md">
        <Link to="/" className="inline-block mb-8"><Logo /></Link>
        <div className="card p-6 sm:p-8">
          <h1 className="text-xl font-bold mb-6">{title}</h1>
          {children}
        </div>
      </div>
    </div>
  );
}

export function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(email, password);
      nav("/app");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Sign in">
      <form onSubmit={submit} className="space-y-4">
        {err && <ErrorBox error={err} />}
        <div><label className="label">Email</label><input className="input mt-1" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div><label className="label">Password</label><input className="input mt-1" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="text-sm text-zinc-500 text-center">No account? <Link className="text-accent hover:underline" to="/register">Create one</Link></p>
      </form>
    </Shell>
  );
}

export function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({ email: "", password: "", name: "", accept: false });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await register(f.email, f.password, f.name, f.accept);
      nav("/app");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Create your account">
      <form onSubmit={submit} className="space-y-4">
        {err && <ErrorBox error={err} />}
        <div><label className="label">Name</label><input className="input mt-1" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div><label className="label">Email</label><input className="input mt-1" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
        <div>
          <label className="label">Password</label>
          <input className="input mt-1" type="password" autoComplete="new-password" minLength={10} required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          <p className="text-xs text-zinc-500 mt-1">At least 10 characters, letters and digits.</p>
        </div>
        <label className="flex gap-3 items-start text-sm text-zinc-300 cursor-pointer">
          <input type="checkbox" className="mt-1 accent-[#ff2a3d]" checked={f.accept} onChange={(e) => setF({ ...f, accept: e.target.checked })} required />
          <span>I understand that CRYPTONEX AI provides analysis only, guarantees no profits, and that I execute all trades myself at my own risk.</span>
        </label>
        <button className="btn-primary w-full" disabled={busy || !f.accept}>{busy ? "Creating…" : "Create account"}</button>
        <RiskNote />
        <p className="text-sm text-zinc-500 text-center">Have an account? <Link className="text-accent hover:underline" to="/login">Sign in</Link></p>
      </form>
    </Shell>
  );
}
