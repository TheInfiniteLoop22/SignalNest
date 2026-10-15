import type { Timings } from "../api/types";

export type PhaseKey = "dns" | "connect" | "tls" | "ttfb" | "body";

export interface Segment {
  key: PhaseKey;
  label: string;
  ms: number;
  /** Start offset and width as percentages of the bar (0-100). */
  offsetPct: number;
  widthPct: number;
}

const LABELS: Record<PhaseKey, string> = {
  dns: "DNS",
  connect: "Connect",
  tls: "TLS",
  ttfb: "Wait (TTFB)",
  body: "Body / other",
};

/**
 * Turn per-phase timings into laid-out segments.
 *
 * Phases that did not happen (null) are omitted. The remainder of `total_ms` that is not
 * explained by a measured phase is shown as "Body / other" so the bar always ends at the
 * total. If the measured phases already exceed the total (clock granularity), the total is
 * extended to their sum rather than drawing something that overflows.
 */
export function computeWaterfall(t: Timings): { segments: Segment[]; totalMs: number } {
  const measured: [PhaseKey, number][] = [];
  const add = (key: PhaseKey, v: number | null) => {
    if (v !== null && v >= 0) measured.push([key, v]);
  };
  add("dns", t.dns_ms);
  add("connect", t.connect_ms);
  add("tls", t.tls_ms);
  add("ttfb", t.ttfb_ms);

  const sum = measured.reduce((a, [, v]) => a + v, 0);
  const total = Math.max(t.total_ms ?? 0, sum);
  const rest = total - sum;
  if (rest > 0) measured.push(["body", rest]);
  if (total === 0) return { segments: [], totalMs: 0 };

  let offset = 0;
  const segments: Segment[] = measured.map(([key, ms]) => {
    const seg: Segment = {
      key,
      label: LABELS[key],
      ms,
      offsetPct: (offset / total) * 100,
      widthPct: (ms / total) * 100,
    };
    offset += ms;
    return seg;
  });
  return { segments, totalMs: total };
}
