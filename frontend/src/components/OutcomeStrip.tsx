import type { RunSummary } from "../api/types";
import { outcomeLabel } from "./Badge";

/** Oldest -> newest strip of the last runs; each cell has a text alternative. */
export function OutcomeStrip({ runs, max = 50 }: { runs: RunSummary[]; max?: number }) {
  const ordered = [...runs].slice(0, max).reverse();
  if (ordered.length === 0) return <span className="muted">no runs yet</span>;
  return (
    <span className="strip" role="img" aria-label={`Last ${ordered.length} runs, oldest first`}>
      {ordered.map((r) => (
        <span
          key={r.id}
          className={`strip-cell outcome-${r.outcome.toLowerCase()}`}
          title={`${outcomeLabel(r.outcome)}${r.failure_class ? ` (${r.failure_class})` : ""}`}
        />
      ))}
    </span>
  );
}
