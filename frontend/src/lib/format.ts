export function formatMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 10_000) return `${(v / 1000).toFixed(1)} s`;
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${Math.round(v)} ms`;
}

export function formatPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(v === 100 ? 0 : 2)}%`;
}

export function localTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
