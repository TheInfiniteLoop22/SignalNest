import { Link } from "react-router-dom";
import { useIncidents } from "../api/hooks";
import { Badge } from "../components/Badge";
import { ErrorBox, Loading } from "../components/Common";
import { localTime } from "../lib/format";

export function IncidentsPage() {
  const { data, isLoading, error } = useIncidents();
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
          <p className="subtitle">Opened automatically when checks keep failing, resolved when they recover.</p>
        </div>
      </div>
      {isLoading && <Loading />}
      {error && <ErrorBox error={error} />}
      {data && data.length === 0 && (
        <div className="empty">
          <img src="/ok.svg" alt="" />
          <h2>No incidents</h2>
          <p>No incidents. That is the good outcome.</p>
        </div>
      )}
      {data && data.length > 0 && (
        <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Status</th>
              <th>Monitor</th>
              <th>Opened</th>
              <th>Resolved</th>
              <th>Cause</th>
            </tr>
          </thead>
          <tbody>
            {data.map((i) => (
              <tr key={i.id}>
                <td>
                  <Badge kind={i.status} />
                </td>
                <td>
                  <Link to={`/monitors/${i.monitor_id}`}>{i.monitor_name}</Link>
                </td>
                <td>{localTime(i.opened_at)}</td>
                <td>{i.resolved_at ? localTime(i.resolved_at) : "—"}</td>
                <td>{i.failure_class}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </>
  );
}
