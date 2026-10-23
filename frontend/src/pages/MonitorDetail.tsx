import { Link, useNavigate, useParams } from "react-router-dom";
import { useDeleteMonitor, useMonitor, useRuns } from "../api/hooks";
import { Badge } from "../components/Badge";
import { ErrorBox, Loading } from "../components/Common";
import { ResponseChart } from "../components/ResponseChart";
import { formatMs, formatPct, localTime } from "../lib/format";

export function MonitorDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const monitor = useMonitor(id);
  const runs = useRuns(id);
  const del = useDeleteMonitor();

  if (monitor.isLoading) return <Loading />;
  if (monitor.error) return <ErrorBox error={monitor.error} />;
  const m = monitor.data;
  if (!m) return null;

  async function remove() {
    if (!confirm(`Delete "${m?.name}" and all of its history?`)) return;
    try {
      await del.mutateAsync(id);
      navigate("/");
    } catch {
      /* the reason is shown below, from del.error */
    }
  }

  return (
    <>
      <div className="breadcrumb">
        <Link to="/">← All monitors</Link>
      </div>
      <div className="page-head">
        <h1>{m.name}</h1>
        <Badge kind={m.status} />
      </div>
      <p className="meta">
        <code>{m.url}</code>
      </p>
      <div className="stats">
        <div className="stat">
          <span className="stat-label">Uptime (24 h)</span>
          <span className="stat-value">{formatPct(m.uptime_24h_pct)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Check every</span>
          <span className="stat-value">{m.interval_seconds}s</span>
        </div>
        <div className="stat">
          <span className="stat-label">Timeout</span>
          <span className="stat-value">{formatMs(m.timeout_ms)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Last check</span>
          <span className="stat-value small">{m.last_checked_at ? localTime(m.last_checked_at) : "not yet"}</span>
        </div>
      </div>

      {del.error && <ErrorBox error={del.error} />}

      <div className="actions">
        <Link className="button secondary" to={`/monitors/${id}/edit`}>
          Edit
        </Link>
        <button className="danger" onClick={() => void remove()}>
          Delete
        </button>
      </div>

      {runs.data && runs.data.length > 1 && (
        <section className="card">
          <h2>Response time</h2>
          <ResponseChart runs={runs.data} />
        </section>
      )}

      <section className="card">
        <h2>Recent runs</h2>
        {runs.isLoading && <Loading />}
        {runs.error && <ErrorBox error={runs.error} />}
        {runs.data && runs.data.length === 0 && <p className="muted">No runs yet.</p>}
        {runs.data && runs.data.length > 0 && (
          <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Time</th>
                <th>Result</th>
                <th>Failure</th>
                <th>HTTP</th>
                <th>DNS</th>
                <th>Connect</th>
                <th>TLS</th>
                <th>First byte</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id} title={r.detail ?? undefined}>
                  <td>{localTime(r.started_at)}</td>
                  <td>
                    <Badge kind={r.outcome} />
                  </td>
                  <td>{r.failure_class ?? "—"}</td>
                  <td>{r.http_status ?? "—"}</td>
                  <td>{formatMs(r.dns_ms)}</td>
                  <td>{formatMs(r.connect_ms)}</td>
                  <td>{formatMs(r.tls_ms)}</td>
                  <td>{formatMs(r.ttfb_ms)}</td>
                  <td>{formatMs(r.total_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </>
  );
}
