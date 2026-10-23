import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { keys, useMe } from "../api/hooks";
import type { User } from "../api/types";

/** RequireAuth sends visitors here with the page they wanted in `state.from`. Only same-site paths count. */
function landingPath(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from;
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/";
}

export function LoginPage({ expired = false }: { expired?: boolean }) {
  const { data: user, isLoading } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const landing = landingPath(useLocation().state);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!isLoading && user) return <Navigate to={landing} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "register") {
        await api<User>("/auth/register", { method: "POST", body: { email, password } });
      }
      await api<void>("/auth/login", { method: "POST", body: { email, password } });
      await qc.invalidateQueries({ queryKey: keys.me });
      navigate(landing, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <aside className="auth-hero" aria-hidden="true">
        <span className="brand">
          <img src="/logo.svg" alt="" />
          SignalNest
        </span>
        <h2>Know the moment your service goes down.</h2>
        <p>
          Watch your endpoints around the clock, get an incident the moment checks start failing, and see exactly
          where the time went.
        </p>
        <img className="hero" src="/hero.svg" alt="" />
        <ul>
          <li>HTTP checks with DNS, connect, TLS and first-byte timings</li>
          <li>Automatic incidents with webhook alerts to Slack or Discord</li>
          <li>Built-in protection against SSRF and brute-force logins</li>
        </ul>
      </aside>
      <main className="auth">
        <div className="auth-card">
          <span className="brand auth-mobile-brand">
            <img src="/logo.svg" alt="" />
            SignalNest
          </span>
          <div>
            <h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1>
            <p className="subtitle">
              {mode === "login" ? "Sign in to see how your services are doing." : "Start monitoring in under a minute."}
            </p>
          </div>
          {expired && (
            <p role="status" className="notice">
              Your session expired. Please sign in again.
            </p>
          )}
          <form onSubmit={(e) => void submit(e)} aria-label={mode === "login" ? "Sign in" : "Create account"}>
            <label>
              Email
              <input
                type="email"
                autoComplete="username"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={mode === "register" ? 12 : 1}
                required
              />
            </label>
            {mode === "register" && <p className="muted small">At least 12 characters.</p>}
            {error && (
              <p role="alert" className="field-error">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy}>
              {mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>
          <p className="auth-switch">
            {mode === "login" ? "New here? " : "Already registered? "}
            <button className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "Create an account" : "I already have an account"}
            </button>
          </p>
        </div>
      </main>
    </div>
  );
}
