import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { SESSION_EXPIRED_EVENT } from "./api/client";
import { keys, useMe } from "./api/hooks";
import { Layout } from "./components/Layout";
import { Loading } from "./components/Common";
import { IncidentDetailPage, IncidentsPage } from "./pages/Incidents";
import { LoginPage } from "./pages/Login";
import { MonitorDetailPage } from "./pages/MonitorDetail";
import { MonitorFormPage } from "./pages/MonitorForm";
import { MonitorsPage } from "./pages/Monitors";
import { RunDetailPage } from "./pages/RunDetail";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { data: user, isLoading } = useMe();
  const location = useLocation();
  if (isLoading) return <Loading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export function App() {
  const qc = useQueryClient();
  const [expired, setExpired] = useState(false);

  // A 401 on any authenticated request means the session is gone: drop the cached user (the
  // gate redirects to /login) and say why, instead of leaving broken pages on screen.
  useEffect(() => {
    const onExpired = () => {
      setExpired(true);
      qc.setQueryData(keys.me, null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [qc]);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage expired={expired} />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<MonitorsPage />} />
        <Route path="monitors/new" element={<MonitorFormPage />} />
        <Route path="monitors/:id" element={<MonitorDetailPage />} />
        <Route path="monitors/:id/edit" element={<MonitorFormPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="incidents/:incidentId" element={<IncidentDetailPage />} />
        <Route path="*" element={<p>Page not found.</p>} />
      </Route>
    </Routes>
  );
}
