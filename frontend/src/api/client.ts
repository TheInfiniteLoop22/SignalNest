import type { Problem } from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: Problem | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Fired when an authenticated request comes back 401 (session expired or revoked). */
export const SESSION_EXPIRED_EVENT = "signalnest:session-expired";

// Endpoints where a 401 is an expected answer, not an expired session.
const AUTH_PROBES = ["/auth/me", "/auth/login", "/auth/register"];

export async function api<T>(
  path: string,
  init: { method?: string; body?: unknown; params?: Record<string, string | number | undefined> } = {},
): Promise<T> {
  const url = new URL(`/api/v1${path}`, window.location.origin);
  for (const [k, v] of Object.entries(init.params ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }
  const method = init.method ?? "GET";
  const res = await fetch(url.toString(), {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });

  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  let problem: Problem | null = null;
  try {
    problem = (await res.json()) as Problem;
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401 && !AUTH_PROBES.some((p) => path.startsWith(p))) {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  throw new ApiError(res.status, problem, problem?.detail ?? `Request failed (${res.status})`);
}
