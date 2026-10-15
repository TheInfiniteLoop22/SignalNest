import { Link, useParams } from "react-router-dom";
import { useRun } from "../api/hooks";
import { OutcomeBadge } from "../components/Badge";
import { Card, ErrorBox, Loading } from "../components/Common";
import { Waterfall } from "../components/Waterfall";
import { localTime, utcTime } from "../lib/format";

function show(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v);
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const { data: r, isLoading, error } = useRun(Number(runId));
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!r) return null;

  const passedAfterRetry = r.outcome === "PASS" && r.attempt_count > 1;

  return (
    <>
      <p>
        <Link to={`/monitors/${r.monitor_id}`}>← Monitor</Link>
      </p>
      <div className="page-head">
        <h1>Run #{r.id}</h1>
        <OutcomeBadge outcome={r.outcome} />
        {r.failure_class && <code>{r.failure_class}</code>}
      </div>
      <p>{r.explanation}</p>
      {passedAfterRetry && (
        <p className="notice" role="note">
          Passed after retry: the first attempt failed ({r.attempts[0]?.error_class ?? "error"}).
        </p>
      )}
      <p className="muted">
        <time title={utcTime(r.started_at)}>{localTime(r.started_at)}</time> · {r.trigger} · worker {r.worker_id}
        {r.resolved_ip && <> · {r.resolved_ip}</>}
        {r.http_status !== null && <> · HTTP {r.http_status}</>}
      </p>

      <Card title="Phases">
        {r.attempts.length > 1 ? (
          r.attempts.map((a, i) => (
            <div key={i}>
              <h3>
                Attempt {i + 1}
                {a.error_class && <> — {a.error_class}</>}
              </h3>
              <Waterfall
                label={`Attempt ${i + 1} phases`}
                timings={{
                  dns_ms: a.dns_ms,
                  connect_ms: a.connect_ms,
                  tls_ms: a.tls_ms,
                  ttfb_ms: a.ttfb_ms,
                  total_ms: a.total_ms,
                }}
              />
            </div>
          ))
        ) : (
          <Waterfall timings={r.timings} />
        )}
      </Card>

      {r.assertion_results.length > 0 && (
        <Card title="Assertions">
          <table className="data">
            <thead>
              <tr>
                <th>Assertion</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {r.assertion_results.map((a, i) => (
                <tr key={i}>
                  <td>{a.type}</td>
                  <td>
                    <code>{show(a.expected)}</code>
                  </td>
                  <td>
                    <code>{show(a.actual)}</code>
                  </td>
                  <td>{a.passed ? "Passed" : "Failed"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {r.error_detail && (
        <Card title="Error">
          <p>{r.error_detail}</p>
        </Card>
      )}

      {r.response_headers && Object.keys(r.response_headers).length > 0 && (
        <Card title="Response headers (allow-listed)">
          <table className="data">
            <tbody>
              {Object.entries(r.response_headers).map(([k, v]) => (
                <tr key={k}>
                  <th scope="row">{k}</th>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {r.response_snippet !== null && (
        <Card title={`Response snippet${r.body_truncated ? " (truncated)" : ""}`}>
          {/* Rendered as a text node: React escapes it, it is never parsed as HTML. */}
          <pre className="snippet" data-testid="snippet">
            {r.response_snippet}
          </pre>
        </Card>
      )}
    </>
  );
}
