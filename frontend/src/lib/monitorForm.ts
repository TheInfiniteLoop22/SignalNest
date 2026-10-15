import { z } from "zod";
import type { AssertionDef, MonitorInput } from "../api/types";

/**
 * Client-side mirror of the server's monitor validation (docs/06 s3). The server stays the
 * authority; this exists so people get instant, specific feedback.
 */
const HEADER_DENY = new Set(["host", "authorization", "cookie", "connection", "content-length", "transfer-encoding"]);
const TOKEN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

export function parseHeaders(text: string): { headers: Record<string, string>; error?: string } {
  const headers: Record<string, string> = {};
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length > 10) return { headers, error: "At most 10 headers" };
  for (const line of lines) {
    const idx = line.indexOf(":");
    if (idx <= 0) return { headers, error: `Expected "Name: value", got "${line}"` };
    const name = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (!TOKEN.test(name)) return { headers, error: `Invalid header name "${name}"` };
    const lower = name.toLowerCase();
    if (HEADER_DENY.has(lower) || lower.startsWith("proxy-")) {
      return { headers, error: `Header "${name}" is not allowed` };
    }
    if (value.length > 512) return { headers, error: `Value of "${name}" is too long` };
    headers[name] = value;
  }
  return { headers };
}

const optionalInt = z
  .union([z.literal(""), z.coerce.number().int().positive()])
  .transform((v) => (v === "" ? undefined : v));

export const monitorFormSchema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(100, "At most 100 characters"),
    url: z
      .string()
      .trim()
      .min(1, "URL is required")
      .max(2048, "At most 2048 characters")
      .refine((v) => {
        try {
          const u = new URL(v);
          return (u.protocol === "http:" || u.protocol === "https:") && !u.username && !u.password;
        } catch {
          return false;
        }
      }, "Enter an http(s) URL without credentials"),
    method: z.enum(["GET", "HEAD"]),
    interval_seconds: z.coerce.number().int().min(30, "Minimum is 30 s").max(3600, "Maximum is 3600 s"),
    timeout_ms: z.coerce.number().int().min(1000, "Minimum is 1000 ms").max(30000, "Maximum is 30000 ms"),
    degraded_ms: optionalInt,
    failure_threshold: z.coerce.number().int().min(1).max(10),
    recovery_threshold: z.coerce.number().int().min(1).max(10),
    headers_text: z.string(),
    // Assertions kept deliberately simple in the UI; the API accepts the full set.
    status_values: z.string().trim(),
    max_latency_ms: optionalInt,
    body_contains: z.string().max(200, "At most 200 characters"),
    json_path: z.string().trim(),
    json_value: z.string(),
  })
  .superRefine((v, ctx) => {
    if (v.degraded_ms !== undefined && v.degraded_ms >= v.timeout_ms) {
      ctx.addIssue({ code: "custom", path: ["degraded_ms"], message: "Must be lower than the timeout" });
    }
    const { error } = parseHeaders(v.headers_text);
    if (error) ctx.addIssue({ code: "custom", path: ["headers_text"], message: error });
    if (v.status_values && !v.status_values.split(",").every((s) => /^\s*[1-5]\d\d\s*$/.test(s))) {
      ctx.addIssue({ code: "custom", path: ["status_values"], message: "Comma-separated HTTP codes, e.g. 200, 204" });
    }
    if (v.json_value !== "" && v.json_path === "") {
      ctx.addIssue({ code: "custom", path: ["json_path"], message: "Path is required when a value is set" });
    }
  });

export type MonitorFormValues = z.input<typeof monitorFormSchema>;
export type MonitorFormParsed = z.output<typeof monitorFormSchema>;

function scalar(text: string): string | number | boolean | null {
  if (text === "true") return true;
  if (text === "false") return false;
  if (text === "null") return null;
  if (text.trim() !== "" && !Number.isNaN(Number(text))) return Number(text);
  return text;
}

export function toMonitorInput(v: MonitorFormParsed): MonitorInput {
  const assertions: AssertionDef[] = [];
  if (v.status_values) {
    assertions.push({ type: "status_in", values: v.status_values.split(",").map((s) => Number(s.trim())) });
  }
  if (v.max_latency_ms !== undefined) assertions.push({ type: "latency_below", ms: v.max_latency_ms });
  if (v.body_contains) assertions.push({ type: "body_contains", text: v.body_contains });
  if (v.json_path) assertions.push({ type: "json_equals", path: v.json_path, value: scalar(v.json_value) });
  return {
    name: v.name,
    url: v.url,
    method: v.method,
    interval_seconds: v.interval_seconds,
    timeout_ms: v.timeout_ms,
    headers: parseHeaders(v.headers_text).headers,
    assertions,
    degraded_ms: v.degraded_ms ?? null,
    failure_threshold: v.failure_threshold,
    recovery_threshold: v.recovery_threshold,
  };
}

/** Inverse of toMonitorInput, for the edit form. */
export function fromMonitor(m: MonitorInput): MonitorFormValues {
  const status = m.assertions.find((a) => a.type === "status_in");
  const lat = m.assertions.find((a) => a.type === "latency_below");
  const body = m.assertions.find((a) => a.type === "body_contains");
  const json = m.assertions.find((a) => a.type === "json_equals");
  return {
    name: m.name,
    url: m.url,
    method: m.method,
    interval_seconds: m.interval_seconds,
    timeout_ms: m.timeout_ms,
    degraded_ms: m.degraded_ms ?? "",
    failure_threshold: m.failure_threshold,
    recovery_threshold: m.recovery_threshold,
    headers_text: Object.entries(m.headers).map(([k, v]) => `${k}: ${v}`).join("\n"),
    status_values: status?.type === "status_in" ? status.values.join(", ") : "",
    max_latency_ms: lat?.type === "latency_below" ? lat.ms : "",
    body_contains: body?.type === "body_contains" ? body.text : "",
    json_path: json?.type === "json_equals" ? json.path : "",
    json_value: json?.type === "json_equals" ? String(json.value) : "",
  };
}

export const FORM_DEFAULTS: MonitorFormValues = {
  name: "",
  url: "",
  method: "GET",
  interval_seconds: 60,
  timeout_ms: 5000,
  degraded_ms: "",
  failure_threshold: 3,
  recovery_threshold: 2,
  headers_text: "",
  status_values: "",
  max_latency_ms: "",
  body_contains: "",
  json_path: "",
  json_value: "",
};
