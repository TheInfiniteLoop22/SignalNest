export type Status = "UNKNOWN" | "UP" | "DOWN";

export interface User {
  id: string;
  email: string;
}

export interface Monitor {
  id: string;
  name: string;
  url: string;
  interval_seconds: number;
  timeout_ms: number;
  expected_status: number | null;
  body_contains: string | null;
  failure_threshold: number;
  status: Status;
  last_checked_at: string | null;
  created_at: string;
  uptime_24h_pct: number | null;
}

export type MonitorInput = Pick<
  Monitor,
  | "name"
  | "url"
  | "interval_seconds"
  | "timeout_ms"
  | "expected_status"
  | "body_contains"
  | "failure_threshold"
>;

export interface Run {
  id: number;
  started_at: string;
  outcome: "PASS" | "FAIL";
  failure_class: string | null;
  http_status: number | null;
  resolved_ip: string | null;
  dns_ms: number | null;
  connect_ms: number | null;
  tls_ms: number | null;
  ttfb_ms: number | null;
  total_ms: number;
  detail: string | null;
}

export interface Incident {
  id: string;
  monitor_id: string;
  monitor_name: string;
  status: "OPEN" | "RESOLVED";
  opened_at: string;
  resolved_at: string | null;
  failure_class: string;
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
}
