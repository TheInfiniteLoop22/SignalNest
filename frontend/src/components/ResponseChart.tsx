import type { Run } from "../api/types";
import { formatMs } from "../lib/format";

const W = 720;
const H = 200;
const PAD = { l: 52, r: 12, t: 12, b: 24 };

/** Total response time of the recent runs, oldest on the left. Failed runs are marked red. */
export function ResponseChart({ runs }: { runs: Run[] }) {
  const pts = [...runs].reverse();
  if (pts.length < 2) return null;
  const max = Math.max(...pts.map((r) => r.total_ms), 1);
  const top = niceCeil(max);
  const x = (i: number) => PAD.l + (i / (pts.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - v / top) * (H - PAD.t - PAD.b);
  const line = pts.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(r.total_ms).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)} ${H - PAD.b} L${PAD.l} ${H - PAD.b}Z`;
  const ticks = [0, 0.5, 1].map((f) => f * top);

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Response time over the last ${pts.length} checks, peak ${formatMs(max)}`}
    >
      {ticks.map((t) => (
        <g key={t}>
          <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
          <text className="axis" x={PAD.l - 8} y={y(t) + 4} textAnchor="end">
            {formatMs(t)}
          </text>
        </g>
      ))}
      <path className="area" d={area} />
      <path className="line" d={line} />
      {pts.map((r, i) => (
        <circle key={r.id} className={r.outcome === "PASS" ? "pt-pass" : "pt-fail"} cx={x(i)} cy={y(r.total_ms)} r={r.outcome === "PASS" ? 2.5 : 4} />
      ))}
      <text className="axis" x={PAD.l} y={H - 6}>
        older
      </text>
      <text className="axis" x={W - PAD.r} y={H - 6} textAnchor="end">
        latest
      </text>
    </svg>
  );
}

function niceCeil(v: number): number {
  const pow = 10 ** Math.floor(Math.log10(v));
  const n = v / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}
