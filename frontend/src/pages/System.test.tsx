import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemPage, type SystemStatus } from "./System";

function renderWith(status: Partial<SystemStatus>) {
  const full: SystemStatus = {
    workers: [],
    scheduler: { due_count: 0, lag_seconds: 0, leased_count: 0 },
    retention: null,
    inconclusive_last_hour: { count: 0, blocked_by_policy: 0 },
    ...status,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(full), { status: 200, headers: { "Content-Type": "application/json" } })),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SystemPage />
    </QueryClientProvider>,
  );
}

const worker = (id: string, alive: boolean) => ({
  worker_id: id,
  started_at: "2026-10-15T10:00:00Z",
  last_seen_at: "2026-10-15T10:00:05Z",
  seen_ago_s: alive ? 3 : 600,
  alive,
  in_flight: 2,
  version: "0",
  last_error: null,
});

afterEach(() => vi.unstubAllGlobals());

describe("SystemPage", () => {
  it("warns loudly when no worker is alive", async () => {
    renderWith({ workers: [worker("w1", false)] });
    expect(await screen.findByRole("alert")).toHaveTextContent(/nothing is being checked/i);
    expect(screen.getByText("Stale")).toBeInTheDocument();
  });

  it("shows healthy workers without an alert", async () => {
    renderWith({ workers: [worker("w1", true), worker("w2", true)] });
    expect(await screen.findByText("2 / 2")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getAllByText("Alive")).toHaveLength(2);
  });

  it("flags scheduler lag", async () => {
    renderWith({ workers: [worker("w1", true)], scheduler: { due_count: 12, lag_seconds: 95, leased_count: 3 } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/behind/i);
  });

  it("explains inconclusive runs as our side, not the target", async () => {
    renderWith({ workers: [worker("w1", true)], inconclusive_last_hour: { count: 4, blocked_by_policy: 1 } });
    expect(await screen.findByRole("note")).toHaveTextContent(/could not be judged/i);
  });

  it("reports retention failures", async () => {
    renderWith({
      workers: [worker("w1", true)],
      retention: { started_at: "2026-10-15T09:00:00Z", finished_at: null, rows_affected: 0, error: "disk full" },
    });
    expect(await screen.findByText(/disk full/)).toBeInTheDocument();
  });
});
