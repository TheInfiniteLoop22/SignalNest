import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { LIVE_REFETCH_MS } from "../api/hooks";
import { Card, ErrorBox, Loading, Stat } from "../components/Common";
import { formatMs, localTime, relativeTime, utcTime } from "../lib/format";

export interface SystemStatus {
  workers: {
    worker_id: string;
    started_at: string;
    last_seen_at: string;
    seen_ago_s: number;
    alive: boolean;
    in_flight: number;
    version: string;
    last_error: string | null;
  }[];
  scheduler: { due_count: number; lag_seconds: number; leased_count: number };
  retention: { started_at: string; finished_at: string | null; rows_affected: number; error: string | null } | null;
  inconclusive_last_hour: { count: number; blocked_by_policy: number };
}

/** Lag above this many seconds means workers are not keeping up (or are not running). */
export const LAG_WARN_S = 30;

export function SystemPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["system"],
    queryFn: () => api<SystemStatus>("/system/status"),
    refetchInterval: LIVE_REFETCH_MS,
  });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const alive = data.workers.filter((w) => w.alive).length;
  const lagging = data.scheduler.lag_seconds > LAG_WARN_S;

  return (
    <>
      <h1>System</h1>
      <p className="muted">Is the monitor itself healthy? This page is about SignalNest, not your targets.</p>

      {alive === 0 && (
        <div role="alert" className="banner">
          No worker has reported in the last 30 seconds. Nothing is being checked.
        </div>
      )}
      {lagging && alive > 0 && (
        <div role="alert" className="banner">
          The scheduler is {formatMs(data.scheduler.lag_seconds * 1000)} behind: workers are not keeping up.
        </div>
      )}
      {data.inconclusive_last_hour.count > 0 && (
        <div role="note" className="notice">
          {data.inconclusive_last_hour.count} check(s) in the last hour could not be judged (our side, or blocked by
          policy). They are excluded from uptime and incidents, so monitor health may be out of date.
        </div>
      )}

      <div className="stats">
        <Stat label="Workers alive" value={`${alive} / ${data.workers.length}`} />
        <Stat label="Due now" value={data.scheduler.due_count} />
        <Stat label="Scheduler lag" value={formatMs(data.scheduler.lag_seconds * 1000)} />
        <Stat label="Probes leased" value={data.scheduler.leased_count} />
      </div>

      <Card title="Workers">
        {data.workers.length === 0 ? (
          <p className="muted">No worker has ever reported.</p>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Worker</th>
                <th>Status</th>
                <th>Started</th>
                <th>Last seen</th>
                <th>In flight</th>
                <th>Last error</th>
              </tr>
            </thead>
            <tbody>
              {data.workers.map((w) => (
                <tr key={w.worker_id}>
                  <td>{w.worker_id}</td>
                  <td>
                    <span className={`badge ${w.alive ? "health-up" : "health-down"}`}>
                      <span aria-hidden="true" className="dot" />
                      {w.alive ? "Alive" : "Stale"}
                    </span>
                  </td>
                  <td title={utcTime(w.started_at)}>{localTime(w.started_at)}</td>
                  <td title={utcTime(w.last_seen_at)}>{relativeTime(w.last_seen_at)}</td>
                  <td>{w.in_flight}</td>
                  <td>{w.last_error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Housekeeping">
        {data.retention ? (
          <p>
            Last retention run <time title={utcTime(data.retention.started_at)}>{relativeTime(data.retention.started_at)}</time>
            : {data.retention.rows_affected} rows removed
            {data.retention.error ? <span className="field-error"> — failed: {data.retention.error}</span> : null}
          </p>
        ) : (
          <p className="muted">Retention has not run yet.</p>
        )}
      </Card>
    </>
  );
}
