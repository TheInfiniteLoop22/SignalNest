import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { api, ApiError } from "./api/client";

type Handler = (url: URL, init: RequestInit) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function mockFetch(handler: Handler) {
  const spy = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) =>
    handler(new URL(String(input)), init),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

function renderApp(path = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const monitor = {
  id: "m1",
  name: "Orders API",
  url: "https://api.example.com/orders",
  method: "GET",
  interval_seconds: 60,
  timeout_ms: 5000,
  headers: {},
  assertions: [],
  degraded_ms: null,
  failure_threshold: 3,
  recovery_threshold: 2,
  is_paused: false,
  created_at: "2026-10-15T10:00:00Z",
  state: {
    health: "DOWN",
    next_run_at: "2026-10-15T10:01:00Z",
    last_run_at: "2026-10-15T10:00:00Z",
    last_failure_class: "READ_TIMEOUT",
    open_incident_id: null,
  },
  summary_24h: { uptime_pct: 98.5, p95_ms: 240, sample_count: 100 },
};

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the CSRF header and parses problem+json", async () => {
    const spy = mockFetch(() =>
      json({ type: "x/validation", title: "Validation failed", status: 422, detail: "bad", errors: [{ field: "url", message: "nope" }] }, 422),
    );
    const err = await api("/monitors", { method: "POST", body: { a: 1 } }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).fieldErrors).toEqual({ url: "nope" });
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe("signalnest");
  });
});

describe("App", () => {
  it("redirects to the login page when there is no session", async () => {
    mockFetch((url) => (url.pathname.endsWith("/auth/me") ? json({ detail: "no" }, 401) : json({}, 404)));
    renderApp("/");
    expect(await screen.findByRole("form", { name: /sign in/i })).toBeInTheDocument();
  });

  it("lists monitors with a text health label and last failure class", async () => {
    mockFetch((url) => {
      if (url.pathname.endsWith("/auth/me")) return json({ id: "u", email: "a@example.com" });
      if (url.pathname.endsWith("/monitors")) return json([monitor]);
      if (url.pathname.endsWith("/runs")) return json({ items: [], next_cursor: null });
      return json({}, 404);
    });
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Orders API" })).toBeInTheDocument();
    expect(screen.getByTestId("health-badge")).toHaveTextContent("Down");
    expect(screen.getByText("READ_TIMEOUT")).toBeInTheDocument();
    expect(screen.getByText("98.50%")).toBeInTheDocument();
    expect(screen.getByTestId("user-email")).toHaveTextContent("a@example.com");
  });

  it("shows a helpful empty state", async () => {
    mockFetch((url) =>
      url.pathname.endsWith("/auth/me") ? json({ id: "u", email: "a@example.com" }) : json([]),
    );
    renderApp("/");
    expect(await screen.findByText(/no monitors yet/i)).toBeInTheDocument();
    expect(screen.getByText(/faultline/i)).toBeInTheDocument();
  });

  it("returns to login with an explanation when a request comes back 401 mid-session", async () => {
    let calls = 0;
    mockFetch((url) => {
      if (url.pathname.endsWith("/auth/me")) {
        // Authenticated the first time; after the session dies, /auth/me says 401 too.
        calls += 1;
        return calls === 1 ? json({ id: "u", email: "a@example.com" }) : json({ detail: "no" }, 401);
      }
      if (url.pathname.endsWith("/monitors")) return json({ detail: "authentication required" }, 401);
      return json({}, 404);
    });
    renderApp("/");
    expect(await screen.findByRole("form", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByText(/session expired/i)).toBeInTheDocument();
  });

  it("renders the response snippet as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
    mockFetch((url) => {
      if (url.pathname.endsWith("/auth/me")) return json({ id: "u", email: "a@example.com" });
      if (url.pathname.endsWith("/runs/7"))
        return json({
          id: 7,
          monitor_id: "m1",
          scheduled_for: "2026-10-15T10:00:00Z",
          started_at: "2026-10-15T10:00:00Z",
          finished_at: "2026-10-15T10:00:01Z",
          outcome: "FAIL",
          failure_class: "ASSERTION_FAILED",
          explanation: "The endpoint answered, but at least one assertion did not hold.",
          timings: { dns_ms: 1, connect_ms: 2, tls_ms: null, ttfb_ms: 30, total_ms: 40 },
          resolved_ip: "93.184.216.34",
          attempt_count: 1,
          attempts: [],
          http_status: 200,
          assertion_results: [{ type: "json_equals", expected: "up", actual: "down", passed: false }],
          response_headers: { "content-type": "text/html" },
          response_snippet: hostile,
          body_truncated: false,
          error_detail: null,
          trigger: "schedule",
          worker_id: "w1",
        });
      return json({}, 404);
    });
    renderApp("/runs/7");
    const pre = await screen.findByTestId("snippet");
    expect(pre.textContent).toBe(hostile);
    expect(pre.querySelector("img, script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("signs in and lands on the monitors page", async () => {
    let signedIn = false;
    mockFetch((url, init) => {
      if (url.pathname.endsWith("/auth/me")) {
        return signedIn ? json({ id: "u", email: "a@example.com" }) : json({ detail: "no" }, 401);
      }
      if (url.pathname.endsWith("/auth/login") && init.method === "POST") {
        signedIn = true;
        return json(null, 204);
      }
      if (url.pathname.endsWith("/monitors")) return json([]);
      return json({}, 404);
    });
    renderApp("/login");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/email/i), "a@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(screen.getByText(/no monitors yet/i)).toBeInTheDocument());
  });
});
