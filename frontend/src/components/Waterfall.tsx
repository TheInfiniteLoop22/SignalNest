import type { Timings } from "../api/types";
import { formatMs } from "../lib/format";
import { computeWaterfall } from "../lib/waterfall";

/**
 * Phase waterfall for one attempt: DNS -> connect -> TLS -> TTFB -> body.
 * Plain divs, no chart library. The table below the bar repeats every number as text so the
 * information never depends on colour or bar width.
 */
export function Waterfall({ timings, label = "Request phases" }: { timings: Timings; label?: string }) {
  const { segments, totalMs } = computeWaterfall(timings);
  if (segments.length === 0) {
    return <p className="muted">No timing data for this attempt.</p>;
  }
  return (
    <figure className="waterfall" aria-label={label}>
      <div className="waterfall-track" role="img" aria-label={`Total ${formatMs(totalMs)}`}>
        {segments.map((s) => (
          <div
            key={s.key}
            className={`wf-seg wf-${s.key}`}
            data-testid={`wf-${s.key}`}
            style={{ left: `${s.offsetPct}%`, width: `${Math.max(s.widthPct, 0.5)}%` }}
            title={`${s.label}: ${formatMs(s.ms)}`}
          />
        ))}
      </div>
      <table className="waterfall-table">
        <tbody>
          {segments.map((s) => (
            <tr key={s.key}>
              <th scope="row">
                <span className={`swatch wf-${s.key}`} aria-hidden="true" /> {s.label}
              </th>
              <td>{formatMs(s.ms)}</td>
            </tr>
          ))}
          <tr className="total">
            <th scope="row">Total</th>
            <td>{formatMs(totalMs)}</td>
          </tr>
        </tbody>
      </table>
    </figure>
  );
}
