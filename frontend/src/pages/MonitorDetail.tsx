import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  useDeleteMonitor,
  useMonitor,
  useMonitorAction,
  useRecentRuns,
  useRuns,
  useStats,
  type RunFilters,
} from "../api/hooks";
import type { Outcome } from "../api/types";
import { HealthBadge, OutcomeBadge } from "../components/Badge";
import { Card, ErrorBox, Loading, Stat } from "../components/Common";
import { OutcomeStrip } from "../components/OutcomeStrip";
import { formatMs, formatPct, localTime, relativeTime, utcTime } from "../lib/format";

function RunsTable({ id }: { id: string }) {
  const [filters, setFilters] = useState<RunFilters>({});
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const cursor = cursors[cursors.length - 1];
  const { data, isLoading, error } = useRuns(id, filters, cursor);

  function changeOutcome(v: string) {
    setCursors([undefined]);
    setFilters(v ? { outcome: v as Outcome } : {});
  }

  return (
    <Card title="Runs">
      <label className="inline">
        Outcome
        <select value={filters.outcome ?? ""} onChange={(e) => changeOutcome(e.target.value)}>
          <option value="">All</option>
          <option value="PASS">Pass</option>
          <option value="DEGRADED">Degraded</option>
          <option value="FAIL">Fail</option>
          <option value="INCONCLUSIVE">Inconclusive</option>
        </select>
      </label>
      {isLoading && <Loading />}
      {error && <ErrorBox error={error} />}
      {data && (
        <>
          <table className="data">
            <thead>
              <tr>
                <th>Time</th>
                <th>Outcome</th>
                <th>Class</th>
                <th>Status</th>
                <th>Total</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.id}>
                  <td title={utcTime(r.started_at)}>{localTime(r.started_at)}</td>
                  <td>
                    <OutcomeBadge outcome={r.outcome} />
                    {r.outcome === "PASS" && r.attempt_count > 1 && <span className="muted"> after retry</span>}
                  </td>
                  <td>{r.failure_class ?? "—"}</td>
                  <td>{r.http_status ?? "—"}</td>
                  <td>{formatMs(r.total_ms)}</td>
                  <td>
                    <Link to={`/runs/${r.id}`}>Details</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.items.length === 0 && <p className="muted">No runs match.</p>}
          <div className="actions">
            <button
              className="secondary"
              disabled={cursors.length <= 1}
              onClick={() => setCursors((c) => c.slice(0, -1))}
            >
              Newer
            </button>
            <button
              className="secondary"
              disabled={!data.next_cursor}
              onClick={() => setCursors((c) => [...c, data.next_cursor ?? undefined])}
            >
              Older
            </button>
          </div>
        </>
      )}
    </Card>
  );
}

export function MonitorDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [window, setWindow] = useState<"24h" | "7d">("24h");
  const monitor = useMonitor(id);
  const stats = useStats(id, window);
  const recent = useRecentRuns(id, 50);
  const actions = useMonitorAction(id);
  const del = useDeleteMonitor();

  if (monitor.isLoading) return <Loading />;
  if (monitor.error) return <ErrorBox error={monitor.error} />;
  const m = monitor.data;
  if (!m) return null;
  const s = m.state;

  async function remove() {
    if (!confirm(`Delete "${m?.name}" and all of its history?`)) return;
    await del.mutateAsync(id);
    navigate("/");
  }

  return (
    <>
      <div className="page-head">
        <h1>{m.name}</h1>
        {s && <HealthBadge health={s.health} />}
      </div>
      <p className="muted">
        {m.method} <code>{m.url}</code> · every {m.interval_seconds}s · timeout {formatMs(m.timeout_ms)}
        {s?.last_run_at && (
          <>
            {" "}
            · last check{" "}
            <time title={utcTime(s.last_run_at)} dateTime={s.last_run_at}>
              {relativeTime(s.last_run_at)}
            </time>
          </>
        )}
      </p>

      {s?.open_incident_id && (
        <div role="alert" className="banner">
          An incident is open for this monitor.{" "}
          <Link to={`/incidents/${s.open_incident_id}`}>View incident</Link>
        </div>
      )}

      <div className="actions">
        {m.is_paused ? (
          <button onClick={() => actions.resume.mutate()} disabled={actions.resume.isPending}>
            Resume
          </button>
        ) : (
          <>
            <button onClick={() => actions.checkNow.mutate()} disabled={actions.checkNow.isPending}>
              Check now
            </button>
            <button className="secondary" onClick={() => actions.pause.mutate()} disabled={actions.pause.isPending}>
              Pause
            </button>
          </>
        )}
        <Link className="button secondary" to={`/monitors/${id}/edit`}>
          Edit
        </Link>
        <button className="danger" onClick={() => void remove()}>
          Delete
        </button>
      </div>
      {actions.checkNow.error && <ErrorBox error={actions.checkNow.error} />}
      {actions.checkNow.isSuccess && <p role="status" className="muted">Check scheduled; the worker will run it within a few seconds.</p>}

      <Card title="Recent runs">
        {recent.data ? <OutcomeStrip runs={recent.data.items} /> : <Loading />}
      </Card>

      <Card title="Statistics">
        <div className="actions">
          <button className={window === "24h" ? "" : "secondary"} onClick={() => setWindow("24h")}>
            24 hours
          </button>
          <button className={window === "7d" ? "" : "secondary"} onClick={() => setWindow("7d")}>
            7 days
          </button>
        </div>
        {stats.isLoading && <Loading />}
        {stats.error && <ErrorBox error={stats.error} />}
        {stats.data && (
          <>
            <div className="stats">
              <Stat label="Uptime" value={formatPct(stats.data.uptime_pct)} />
              <Stat label="p50 latency" value={formatMs(stats.data.p50_ms)} />
              <Stat label="p95 latency" value={formatMs(stats.data.p95_ms)} />
              <Stat label="Checks" value={stats.data.sample_count} />
            </div>
            {stats.data.series.length > 0 ? (
              <div className="chart" aria-label="Latency over time">
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart
                    data={stats.data.series.map((p) => ({
                      t: new Date(p.bucket).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
                      p50: p.p50_ms,
                      p95: p.p95_ms,
                    }))}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="t" />
                    <YAxis unit=" ms" width={70} />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="p50" stroke="#2563eb" dot={false} connectNulls />
                    <Line type="monotone" dataKey="p95" stroke="#d97706" dot={false} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="muted">No data in this window yet.</p>
            )}
            {Object.keys(stats.data.by_failure_class).length > 0 && (
              <>
                <h3>Failure classes</h3>
                <ul className="plain">
                  {Object.entries(stats.data.by_failure_class)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, v]) => (
                      <li key={k}>
                        {k}: <strong>{v}</strong>
                      </li>
                    ))}
                </ul>
              </>
            )}
          </>
        )}
      </Card>

      <RunsTable id={id} />
    </>
  );
}
