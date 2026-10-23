import { Link } from "react-router-dom";
import { useMonitors } from "../api/hooks";
import { Badge } from "../components/Badge";
import { ErrorBox, Loading } from "../components/Common";
import { formatPct, hostOf, localTime } from "../lib/format";

export function MonitorsPage() {
  const { data, isLoading, error } = useMonitors();
  if (isLoading) return <Loading what="Loading monitors" />;
  if (error) return <ErrorBox error={error} />;
  const monitors = data ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Monitors</h1>
        <Link className="button" to="/monitors/new">
          New monitor
        </Link>
      </div>

      {monitors.length === 0 ? (
        <div className="empty">
          <h2>No monitors yet</h2>
          <p>
            <Link to="/monitors/new">Create your first monitor</Link>.
          </p>
        </div>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>Monitor</th>
              <th>Status</th>
              <th>Last check</th>
              <th>Uptime 24 h</th>
            </tr>
          </thead>
          <tbody>
            {monitors.map((m) => (
              <tr key={m.id}>
                <td>
                  <Link to={`/monitors/${m.id}`}>{m.name}</Link>
                  <div className="muted small">{hostOf(m.url)}</div>
                </td>
                <td>
                  <Badge kind={m.status} />
                </td>
                <td>
                  {m.last_checked_at ? localTime(m.last_checked_at) : <span className="muted">not yet</span>}
                </td>
                <td>{formatPct(m.uptime_24h_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
