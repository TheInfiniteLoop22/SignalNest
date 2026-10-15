import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useIncident, useIncidents } from "../api/hooks";
import { Card, ErrorBox, Loading } from "../components/Common";
import { localTime, utcTime } from "../lib/format";

export function IncidentsPage() {
  const [status, setStatus] = useState<"" | "OPEN" | "RESOLVED">("");
  const { data, isLoading, error } = useIncidents(status || undefined);
  return (
    <>
      <div className="page-head">
        <h1>Incidents</h1>
        <label className="inline">
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="">All</option>
            <option value="OPEN">Open</option>
            <option value="RESOLVED">Resolved</option>
          </select>
        </label>
      </div>
      {isLoading && <Loading />}
      {error && <ErrorBox error={error} />}
      {data && data.items.length === 0 && <p className="muted">No incidents. That is the good outcome.</p>}
      {data && data.items.length > 0 && (
        <table className="data">
          <thead>
            <tr>
              <th>Status</th>
              <th>Opened</th>
              <th>Resolved</th>
              <th>Failures</th>
              <th>Main cause</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.items.map((i) => (
              <tr key={i.id}>
                <td>
                  <span className={`badge incident-${i.status.toLowerCase()}`}>
                    <span aria-hidden="true" className="dot" />
                    {i.status === "OPEN" ? "Open" : "Resolved"}
                  </span>
                </td>
                <td title={utcTime(i.opened_at)}>{localTime(i.opened_at)}</td>
                <td>{i.resolved_at ? localTime(i.resolved_at) : "—"}</td>
                <td>{i.failure_count}</td>
                <td>{i.dominant_failure_class}</td>
                <td>
                  <Link to={`/incidents/${i.id}`}>Details</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export function IncidentDetailPage() {
  const { incidentId = "" } = useParams();
  const { data: inc, isLoading, error } = useIncident(incidentId);
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!inc) return null;
  const ev = inc.evidence;

  return (
    <>
      <p>
        <Link to={`/monitors/${inc.monitor_id}`}>← Monitor</Link>
      </p>
      <div className="page-head">
        <h1>Incident</h1>
        <span className={`badge incident-${inc.status.toLowerCase()}`}>
          <span aria-hidden="true" className="dot" />
          {inc.status === "OPEN" ? "Open" : "Resolved"}
        </span>
      </div>
      <p className="muted">
        Opened <time title={utcTime(inc.opened_at)}>{localTime(inc.opened_at)}</time>
        {inc.resolved_at && <> · resolved {localTime(inc.resolved_at)}</>} · {inc.failure_count} failed checks ·{" "}
        {inc.dominant_failure_class}
      </p>

      <Card title="Diagnosis hints">
        {ev.hints && ev.hints.length > 0 ? (
          <ul className="plain">
            {ev.hints.map((h) => (
              <li key={h.code}>
                <strong>{h.code}</strong> — {h.text}
                <div className="muted small">
                  Based on runs:{" "}
                  {h.evidence_run_ids.map((id, i) => (
                    <span key={id}>
                      {i > 0 && ", "}
                      <Link to={`/runs/${id}`}>#{id}</Link>
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No rule matched the available evidence.</p>
        )}
      </Card>

      <Card title="Evidence">
        <p>
          First failing run: <Link to={`/runs/${inc.first_failure_run_id}`}>#{inc.first_failure_run_id}</Link>
          {inc.resolving_run_id && (
            <>
              {" "}
              · resolving run: <Link to={`/runs/${inc.resolving_run_id}`}>#{inc.resolving_run_id}</Link>
            </>
          )}
        </p>
        {ev.class_counts && (
          <ul className="plain">
            {Object.entries(ev.class_counts).map(([k, v]) => (
              <li key={k}>
                {k}: <strong>{v}</strong>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Timeline">
        <ol className="timeline">
          {inc.events.map((e, i) => (
            <li key={i}>
              <time title={utcTime(e.at)}>{localTime(e.at)}</time> — <strong>{e.kind.replaceAll("_", " ")}</strong>
              {e.run_id !== null && (
                <>
                  {" "}
                  (<Link to={`/runs/${e.run_id}`}>run #{e.run_id}</Link>)
                </>
              )}
            </li>
          ))}
        </ol>
      </Card>
    </>
  );
}
