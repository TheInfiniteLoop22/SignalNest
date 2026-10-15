import { describe, expect, it } from "vitest";
import { formatMs, formatPct, relativeTime } from "./format";
import { FORM_DEFAULTS, fromMonitor, monitorFormSchema, parseHeaders, toMonitorInput } from "./monitorForm";

const valid = { ...FORM_DEFAULTS, name: "orders", url: "https://api.example.com/orders" };

describe("format", () => {
  it("formats durations", () => {
    expect(formatMs(null)).toBe("—");
    expect(formatMs(42.4)).toBe("42 ms");
    expect(formatMs(1500)).toBe("1.50 s");
    expect(formatMs(12000)).toBe("12.0 s");
  });
  it("formats percentages", () => {
    expect(formatPct(100)).toBe("100%");
    expect(formatPct(99.5)).toBe("99.50%");
    expect(formatPct(null)).toBe("—");
  });
  it("formats relative time both ways", () => {
    const now = Date.parse("2026-10-15T10:00:00Z");
    expect(relativeTime("2026-10-15T09:59:30Z", now)).toBe("30 s ago");
    expect(relativeTime("2026-10-15T09:00:00Z", now)).toBe("1 h ago");
    expect(relativeTime("2026-10-15T10:00:05Z", now)).toBe("in 5 s");
    expect(relativeTime(null, now)).toBe("never");
  });
});

describe("parseHeaders", () => {
  it("parses name: value lines", () => {
    expect(parseHeaders("Accept: application/json\nX-Trace: a:b").headers).toEqual({
      Accept: "application/json",
      "X-Trace": "a:b",
    });
  });
  it.each(["Host: x", "authorization: Bearer x", "Cookie: a=b", "Proxy-Authorization: x", "bad name: x", "nocolon"])(
    "rejects %s",
    (line) => {
      expect(parseHeaders(line).error).toBeTruthy();
    },
  );
  it("caps the number of headers", () => {
    const many = Array.from({ length: 11 }, (_, i) => `X-${i}: v`).join("\n");
    expect(parseHeaders(many).error).toMatch(/at most 10/i);
  });
});

describe("monitorFormSchema", () => {
  it("accepts a minimal monitor", () => {
    expect(monitorFormSchema.safeParse(valid).success).toBe(true);
  });

  it.each([
    [{ name: "" }, "name"],
    [{ url: "ftp://x.example.com" }, "url"],
    [{ url: "https://user:pw@x.example.com" }, "url"],
    [{ url: "not a url" }, "url"],
    [{ interval_seconds: 5 }, "interval_seconds"],
    [{ timeout_ms: 500 }, "timeout_ms"],
    [{ failure_threshold: 11 }, "failure_threshold"],
    [{ timeout_ms: 2000, degraded_ms: 2000 }, "degraded_ms"],
    [{ status_values: "20x" }, "status_values"],
    [{ headers_text: "Host: x" }, "headers_text"],
    [{ json_value: "up", json_path: "" }, "json_path"],
  ])("rejects %j", (patch, field) => {
    const r = monitorFormSchema.safeParse({ ...valid, ...patch });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain(field);
  });

  it("builds the API payload with typed assertions", () => {
    const parsed = monitorFormSchema.parse({
      ...valid,
      degraded_ms: "800",
      status_values: "200, 204",
      max_latency_ms: "2000",
      body_contains: "orders",
      json_path: "items.0.ok",
      json_value: "true",
      headers_text: "Accept: application/json",
    });
    const input = toMonitorInput(parsed);
    expect(input.degraded_ms).toBe(800);
    expect(input.headers).toEqual({ Accept: "application/json" });
    expect(input.assertions).toEqual([
      { type: "status_in", values: [200, 204] },
      { type: "latency_below", ms: 2000 },
      { type: "body_contains", text: "orders" },
      { type: "json_equals", path: "items.0.ok", value: true },
    ]);
  });

  it("round-trips through the edit form", () => {
    const parsed = monitorFormSchema.parse({ ...valid, status_values: "200", json_path: "status", json_value: "ok" });
    const back = monitorFormSchema.parse(fromMonitor(toMonitorInput(parsed)));
    expect(toMonitorInput(back)).toEqual(toMonitorInput(parsed));
  });
});
