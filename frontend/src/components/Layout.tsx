import { useQueryClient } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { keys, useMe } from "../api/hooks";

export function Layout() {
  const { data: user } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();

  async function logout() {
    try {
      await api<void>("/auth/logout", { method: "POST" });
    } finally {
      qc.setQueryData(keys.me, null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      navigate("/login");
    }
  }

  return (
    <>
      <header className="topbar">
        <NavLink to="/" className="brand" aria-label="SignalNest home">
          <img src="/logo.svg" alt="" />
          SignalNest
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Monitors
          </NavLink>
          <NavLink to="/incidents">Incidents</NavLink>
        </nav>
        <span className="spacer" />
        <span className="user-chip">
          <span className="avatar" aria-hidden="true">
            {user?.email.charAt(0)}
          </span>
          <span className="muted" data-testid="user-email">
            {user?.email}
          </span>
        </span>
        <button className="secondary" onClick={() => void logout()}>
          Log out
        </button>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <footer className="footer">SignalNest · uptime monitoring and incident tracking</footer>
    </>
  );
}
