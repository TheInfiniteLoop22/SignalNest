import { useState } from "react";
import { Link } from "react-router-dom";
import { useMonitors, useRecentRuns } from "../api/hooks";
import type { Health, Monitor } from "../api/types";
import { HealthBadge } from "../components/Badge";
import { ErrorBox, Loading } from "../components/Common";
import { OutcomeStrip } from "../components/OutcomeStrip";
import { formatMs, formatPct, hostOf, localTime, relativeTime, utcTime } from "../lib/format";

function Row({ m }: { m: Monitor }) {
  const recent = useRecentRuns(m.id, 50);
  const s = m.state;
  return (
    <tr>
      <td>
        <Link to={`/monitors/${m.id}`}>{m.name}</Link>
        <div className="muted small">{hostOf(m.url)}</div>
      </td>
      <td>{s ? <HealthBadge health={s.health} /> : "—"}</td>
      <td>
        {s?.last_run_at ? (
          <time dateTime={s.last_run_at} title={`${localTime(s.last_run_at)} (${utcTime(s.last_run_at)})`}>
            {relativeTime(s.last_run_at)}
          </time>
        ) : (
          <span className="muted">not yet</span>
        )}
      </td>
      <td>{s?.last_failure_class ?? <span className="muted">—</span>}</td>
      <td>{formatPct(m.summary_24h?.uptime_pct)}</td>
      <td>{formatMs(m.summary_24h?.p95_ms)}</td>
      <td>{recent.data ? <OutcomeStrip runs={recent.data.items} /> : <span className="muted">…</span>}</td>
    </tr>
  );
}

export function MonitorsPage() {
  const { data, isLoading, error } = useMonitors();
  const [filter, setFilter] = useState<Health | "">("");

  if (isLoading) return <Loading what="Loading monitors" />;
  if (error) return <ErrorBox error={error} />;
  const monitors = (data ?? []).filter((m) => !filter || m.state?.health === filter);

  return (
    <>
      <div className="page-head">
        <h1>Monitors</h1>
        <label className="inline">
          Health
          <select value={filter} onChange={(e) => setFilter(e.target.value as Health | "")}>
            <option value="">All</option>
            {(["UP", "DEGRADED", "DOWN", "PAUSED", "UNKNOWN"] as Health[]).map((h) => (
              <option key={h} value={h}>
                {h.charAt(0) + h.slice(1).toLowerCase()}
              </option>
            ))}
          </select>
        </label>
        <Link className="button" to="/monitors/new">
          New monitor
        </Link>
      </div>

      {(data ?? []).length === 0 ? (
        <div className="empty">
          <h2>No monitors yet</h2>
          <p>
            <Link to="/monitors/new">Create your first monitor</Link>, or try the bundled
            fault-injection target: run <code>docker compose up</code> and monitor{" "}
            <code>http://faultline:8001/api/orders</code>.
          </p>
        </div>
      ) : monitors.length === 0 ? (
        <p className="muted">No monitors match this filter.</p>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>Monitor</th>
              <th>Health</th>
              <th>Last check</th>
              <th>Last failure</th>
              <th>Uptime 24 h</th>
              <th>p95 24 h</th>
              <th>Last 50 runs</th>
            </tr>
          </thead>
          <tbody>
            {monitors.map((m) => (
              <Row key={m.id} m={m} />
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
