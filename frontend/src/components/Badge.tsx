import type { Health, Outcome } from "../api/types";

/**
 * Every colour-coded state also carries a text label (colour alone is not accessible).
 * The label is the source of truth; the colour class is decoration.
 */
const HEALTH_LABEL: Record<Health, string> = {
  UNKNOWN: "Unknown",
  UP: "Up",
  DEGRADED: "Degraded",
  DOWN: "Down",
  PAUSED: "Paused",
};

const OUTCOME_LABEL: Record<Outcome, string> = {
  PASS: "Pass",
  DEGRADED: "Degraded",
  FAIL: "Fail",
  INCONCLUSIVE: "Inconclusive",
};

export function HealthBadge({ health }: { health: Health }) {
  return (
    <span className={`badge health-${health.toLowerCase()}`} data-testid="health-badge">
      <span aria-hidden="true" className="dot" />
      {HEALTH_LABEL[health]}
    </span>
  );
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return (
    <span className={`badge outcome-${outcome.toLowerCase()}`} data-testid="outcome-badge">
      <span aria-hidden="true" className="dot" />
      {OUTCOME_LABEL[outcome]}
    </span>
  );
}

export function outcomeLabel(o: Outcome): string {
  return OUTCOME_LABEL[o];
}
