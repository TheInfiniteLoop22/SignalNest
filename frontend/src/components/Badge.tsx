import type { Status } from "../api/types";

/** Colour is decoration; the text label carries the meaning (colour alone is not accessible). */
const LABEL: Record<Status | "OPEN" | "RESOLVED" | "PASS" | "FAIL", string> = {
  UNKNOWN: "Unknown",
  UP: "Up",
  DOWN: "Down",
  OPEN: "Open",
  RESOLVED: "Resolved",
  PASS: "Pass",
  FAIL: "Fail",
};

export function Badge({ kind }: { kind: keyof typeof LABEL }) {
  return (
    <span className={`badge badge-${kind.toLowerCase()}`} data-testid="badge">
      <span aria-hidden="true" className="dot" />
      {LABEL[kind]}
    </span>
  );
}
