import { describe, expect, it } from "vitest";
import { computeWaterfall } from "./waterfall";

const t = (o: Partial<Record<"dns_ms" | "connect_ms" | "tls_ms" | "ttfb_ms" | "total_ms", number | null>>) => ({
  dns_ms: null,
  connect_ms: null,
  tls_ms: null,
  ttfb_ms: null,
  total_ms: null,
  ...o,
});

describe("computeWaterfall", () => {
  it("lays segments out back to back and fills the remainder as body/other", () => {
    const { segments, totalMs } = computeWaterfall(
      t({ dns_ms: 10, connect_ms: 20, tls_ms: 30, ttfb_ms: 100, total_ms: 200 }),
    );
    expect(totalMs).toBe(200);
    expect(segments.map((s) => [s.key, s.ms])).toEqual([
      ["dns", 10],
      ["connect", 20],
      ["tls", 30],
      ["ttfb", 100],
      ["body", 40],
    ]);
    expect(segments[0]?.offsetPct).toBe(0);
    expect(segments[1]?.offsetPct).toBeCloseTo(5);
    const last = segments[segments.length - 1];
    expect((last?.offsetPct ?? 0) + (last?.widthPct ?? 0)).toBeCloseTo(100);
  });

  it("omits phases that did not happen (refused connection has no TLS or TTFB)", () => {
    const { segments } = computeWaterfall(t({ connect_ms: 3, total_ms: 3 }));
    expect(segments.map((s) => s.key)).toEqual(["connect"]);
  });

  it("shows a hang as connect then a long wait remainder", () => {
    const { segments, totalMs } = computeWaterfall(t({ connect_ms: 11, tls_ms: 24, total_ms: 5003 }));
    expect(totalMs).toBe(5003);
    expect(segments.find((s) => s.key === "body")?.ms).toBe(5003 - 35);
  });

  it("extends the total when measured phases exceed it (clock granularity) instead of overflowing", () => {
    const { segments, totalMs } = computeWaterfall(t({ connect_ms: 6, ttfb_ms: 6, total_ms: 10 }));
    expect(totalMs).toBe(12);
    expect(segments.some((s) => s.key === "body")).toBe(false);
    const last = segments[segments.length - 1];
    expect((last?.offsetPct ?? 0) + (last?.widthPct ?? 0)).toBeCloseTo(100);
  });

  it("returns nothing when there is no timing data", () => {
    expect(computeWaterfall(t({})).segments).toEqual([]);
  });

  it("treats zero-length measured phases as present but empty", () => {
    const { segments } = computeWaterfall(t({ dns_ms: 0, connect_ms: 5, total_ms: 5 }));
    expect(segments.map((s) => s.key)).toEqual(["dns", "connect"]);
    expect(segments[0]?.widthPct).toBe(0);
  });
});
