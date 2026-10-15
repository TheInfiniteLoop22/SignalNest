export function formatMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 10_000) return `${(v / 1000).toFixed(1)} s`;
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${Math.round(v)} ms`;
}

export function formatPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(v === 100 ? 0 : 2)}%`;
}

/** "5 s ago", "3 min ago", "2 h ago", "4 d ago"; future times read "in 5 s". */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const diffS = Math.round((now - new Date(iso).getTime()) / 1000);
  const abs = Math.abs(diffS);
  const unit =
    abs < 60
      ? `${abs} s`
      : abs < 3600
        ? `${Math.round(abs / 60)} min`
        : abs < 86400
          ? `${Math.round(abs / 3600)} h`
          : `${Math.round(abs / 86400)} d`;
  return diffS >= 0 ? `${unit} ago` : `in ${unit}`;
}

export function localTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function utcTime(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
