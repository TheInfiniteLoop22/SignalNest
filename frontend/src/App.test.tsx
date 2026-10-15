import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { formatMs, formatPct, hostOf } from "./lib/format";

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

const user = { id: "u1", email: "a@example.com" };
const monitor = {
  id: "m1",
  name: "Orders API",
  url: "https://api.example.com/orders",
  interval_seconds: 60,
  timeout_ms: 5000,
  expected_status: null,
  body_contains: null,
  failure_threshold: 3,
  status: "DOWN",
  last_checked_at: "2026-10-15T10:00:00Z",
  created_at: "2026-10-15T09:00:00Z",
  uptime_24h_pct: 98.5,
};

afterEach(() => vi.unstubAllGlobals());

describe("app", () => {
  it("sends signed-out visitors to the login form", async () => {
    mockFetch(() => json({ type: "x", title: "Unauthenticated", status: 401, detail: "no" }, 401));
    renderApp("/");
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
  });

  it("lists monitors with their status and uptime", async () => {
    mockFetch((url) => {
      if (url.pathname.endsWith("/auth/me")) return json(user);
      if (url.pathname.endsWith("/monitors")) return json([monitor]);
      return json({}, 404);
    });
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Orders API" })).toBeInTheDocument();
    expect(screen.getByTestId("badge")).toHaveTextContent("Down");
    expect(screen.getByText("98.50%")).toBeInTheDocument();
  });

  it("shows the server's reason when a monitor is rejected", async () => {
    mockFetch((url, init) => {
      if (url.pathname.endsWith("/auth/me")) return json(user);
      if (init.method === "POST" && url.pathname.endsWith("/monitors")) {
        return json(
          { type: "blocked-target", title: "Validation failed", status: 422, detail: "port 22 is not allowed" },
          422,
        );
      }
      return json([]);
    });
    renderApp("/monitors/new");
    await userEvent.type(await screen.findByLabelText("Name"), "ssh");
    await userEvent.type(screen.getByLabelText(/URL/), "http://example.com:22/");
    await userEvent.click(screen.getByRole("button", { name: "Create monitor" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("port 22 is not allowed"));
  });

  it("lists incidents, open first as the API orders them", async () => {
    mockFetch((url) => {
      if (url.pathname.endsWith("/auth/me")) return json(user);
      return json([
        {
          id: "i1",
          monitor_id: "m1",
          monitor_name: "Orders API",
          status: "OPEN",
          opened_at: "2026-10-15T10:00:00Z",
          resolved_at: null,
          failure_class: "TIMEOUT",
        },
      ]);
    });
    renderApp("/incidents");
    expect(await screen.findByText("TIMEOUT")).toBeInTheDocument();
    expect(screen.getByTestId("badge")).toHaveTextContent("Open");
  });
});

describe("format", () => {
  it("formats durations, percentages and hosts", () => {
    expect(formatMs(null)).toBe("—");
    expect(formatMs(42.4)).toBe("42 ms");
    expect(formatMs(1500)).toBe("1.50 s");
    expect(formatPct(100)).toBe("100%");
    expect(formatPct(99.123)).toBe("99.12%");
    expect(hostOf("https://api.example.com:8443/x")).toBe("api.example.com:8443");
  });
});
