export type Outcome = "PASS" | "DEGRADED" | "FAIL" | "INCONCLUSIVE";
export type Health = "UNKNOWN" | "UP" | "DEGRADED" | "DOWN" | "PAUSED";

export interface User {
  id: string;
  email: string;
}

export interface MonitorState {
  health: Health;
  next_run_at: string;
  last_run_at: string | null;
  last_failure_class: string | null;
  open_incident_id: string | null;
}

export interface Summary {
  uptime_pct: number | null;
  p95_ms: number | null;
  sample_count: number;
}

export type AssertionDef =
  | { type: "status_in"; values: number[] }
  | { type: "latency_below"; ms: number }
  | { type: "body_contains"; text: string }
  | { type: "json_equals"; path: string; value: string | number | boolean | null };

export interface Monitor {
  id: string;
  name: string;
  url: string;
  method: "GET" | "HEAD";
  interval_seconds: number;
  timeout_ms: number;
  headers: Record<string, string>;
  assertions: AssertionDef[];
  degraded_ms: number | null;
  failure_threshold: number;
  recovery_threshold: number;
  is_paused: boolean;
  created_at: string;
  state: MonitorState | null;
  summary_24h: Summary | null;
}

export type MonitorInput = Omit<Monitor, "id" | "is_paused" | "created_at" | "state" | "summary_24h">;

export interface Timings {
  dns_ms: number | null;
  connect_ms: number | null;
  tls_ms: number | null;
  ttfb_ms: number | null;
  total_ms: number | null;
}

export interface RunSummary extends Timings {
  id: number;
  scheduled_for: string;
  started_at: string;
  finished_at: string;
  outcome: Outcome;
  failure_class: string | null;
  http_status: number | null;
  resolved_ip: string | null;
  attempt_count: number;
  error_detail: string | null;
  trigger: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface AssertionResult {
  type: string;
  expected: unknown;
  actual: unknown;
  passed: boolean;
}

export interface AttemptJson {
  started_at: string;
  resolved_ip: string | null;
  dns_ms: number | null;
  connect_ms: number | null;
  tls_ms: number | null;
  ttfb_ms: number | null;
  total_ms: number;
  http_status: number | null;
  error_class: string | null;
  error_detail: string | null;
}

export interface RunDetail {
  id: number;
  monitor_id: string;
  scheduled_for: string;
  started_at: string;
  finished_at: string;
  outcome: Outcome;
  failure_class: string | null;
  explanation: string;
  timings: Timings;
  resolved_ip: string | null;
  attempt_count: number;
  attempts: AttemptJson[];
  http_status: number | null;
  assertion_results: AssertionResult[];
  response_headers: Record<string, string> | null;
  response_snippet: string | null;
  body_truncated: boolean;
  error_detail: string | null;
  trigger: string;
  worker_id: string;
}

export interface Stats {
  window: string;
  uptime_pct: number | null;
  sample_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  by_outcome: Record<string, number>;
  by_failure_class: Record<string, number>;
}

export interface Incident {
  id: string;
  monitor_id: string;
  status: "OPEN" | "RESOLVED";
  opened_at: string;
  resolved_at: string | null;
  failure_count: number;
  dominant_failure_class: string;
}

export interface IncidentEvent {
  at: string;
  kind: string;
  run_id: number | null;
  detail: Record<string, unknown>;
}

export interface Hint {
  code: string;
  text: string;
  evidence_run_ids: number[];
}

export interface IncidentDetail extends Incident {
  first_failure_run_id: number;
  resolving_run_id: number | null;
  events: IncidentEvent[];
  evidence: {
    class_counts?: Record<string, number>;
    hints?: Hint[];
  };
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  errors?: { field: string; message: string }[];
  request_id?: string;
}
