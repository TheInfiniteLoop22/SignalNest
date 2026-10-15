import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RunSummary } from "../api/types";
import { HealthBadge, OutcomeBadge } from "./Badge";
import { OutcomeStrip } from "./OutcomeStrip";
import { Waterfall } from "./Waterfall";

describe("badges", () => {
  it("always carry a text label, not just a colour", () => {
    render(
      <>
        <HealthBadge health="DOWN" />
        <HealthBadge health="UP" />
        <OutcomeBadge outcome="INCONCLUSIVE" />
      </>,
    );
    expect(screen.getByText("Down")).toBeInTheDocument();
    expect(screen.getByText("Up")).toBeInTheDocument();
    expect(screen.getByText("Inconclusive")).toBeInTheDocument();
  });
});

describe("Waterfall", () => {
  it("renders one bar per phase and repeats every value as text", () => {
    render(<Waterfall timings={{ dns_ms: 2, connect_ms: 11, tls_ms: 24, ttfb_ms: 400, total_ms: 450 }} />);
    for (const key of ["dns", "connect", "tls", "ttfb", "body"]) {
      expect(screen.getByTestId(`wf-${key}`)).toBeInTheDocument();
    }
    const table = screen.getByRole("table");
    expect(within(table).getByText("Connect").closest("tr")).toHaveTextContent("11 ms");
    expect(within(table).getByText("Wait (TTFB)").closest("tr")).toHaveTextContent("400 ms");
    expect(within(table).getByText("Total").closest("tr")).toHaveTextContent("450 ms");
  });

  it("says so when there is no timing data", () => {
    render(<Waterfall timings={{ dns_ms: null, connect_ms: null, tls_ms: null, ttfb_ms: null, total_ms: null }} />);
    expect(screen.getByText(/no timing data/i)).toBeInTheDocument();
  });

  it("sizes the track proportionally", () => {
    render(<Waterfall timings={{ dns_ms: null, connect_ms: 50, tls_ms: null, ttfb_ms: 50, total_ms: 100 }} />);
    expect(screen.getByTestId("wf-connect")).toHaveStyle({ left: "0%", width: "50%" });
    expect(screen.getByTestId("wf-ttfb")).toHaveStyle({ left: "50%", width: "50%" });
  });
});

describe("OutcomeStrip", () => {
  const run = (id: number, outcome: RunSummary["outcome"]): RunSummary => ({
    id,
    scheduled_for: "",
    started_at: "",
    finished_at: "",
    outcome,
    failure_class: outcome === "FAIL" ? "READ_TIMEOUT" : null,
    http_status: null,
    dns_ms: null,
    connect_ms: null,
    tls_ms: null,
    ttfb_ms: null,
    total_ms: null,
    resolved_ip: null,
    attempt_count: 1,
    error_detail: null,
    trigger: "schedule",
  });

  it("shows oldest first with text alternatives", () => {
    // API returns newest first
    render(<OutcomeStrip runs={[run(3, "FAIL"), run(2, "PASS"), run(1, "PASS")]} />);
    const strip = screen.getByRole("img", { name: /last 3 runs, oldest first/i });
    const cells = strip.querySelectorAll(".strip-cell");
    expect(cells).toHaveLength(3);
    expect(cells[2]).toHaveAttribute("title", "Fail (READ_TIMEOUT)");
    expect(cells[0]).toHaveAttribute("title", "Pass");
  });

  it("handles no runs", () => {
    render(<OutcomeStrip runs={[]} />);
    expect(screen.getByText(/no runs yet/i)).toBeInTheDocument();
  });
});
