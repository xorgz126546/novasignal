import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import Admin from "./pages/Admin";
import Agents from "./pages/Agents";
import Analytics from "./pages/Analytics";
import { Login, Register } from "./pages/Auth";
import Journal from "./pages/Journal";
import Landing from "./pages/Landing";
import Overview from "./pages/Overview";
import Settings from "./pages/Settings";
import SignalDetail from "./pages/SignalDetail";
import Signals from "./pages/Signals";
import Validation from "./pages/Validation";

function Protected({ children, admin = false }: { children: React.ReactNode; admin?: boolean }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8"><Loading /></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "admin") return <Navigate to="/app" replace />;
  return <>{children}</>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/app" element={<Protected><Layout /></Protected>}>
            <Route index element={<Overview />} />
            <Route path="signals" element={<Signals />} />
            <Route path="signals/:id" element={<SignalDetail />} />
            <Route path="journal" element={<Journal />} />
            <Route path="agents" element={<Agents />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="validation" element={<Validation />} />
            <Route path="settings" element={<Settings />} />
            <Route path="admin" element={<Protected admin><Admin /></Protected>} />
          </Route>
          <Route path="/signals/:id" element={<RedirectSignal />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </React.StrictMode>,
);

function RedirectSignal() {
  const id = window.location.pathname.split("/").pop();
  return <Navigate to={`/app/signals/${id}`} replace />;
}
