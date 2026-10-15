import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p role="status" className="muted">
      {what}…
    </p>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="error-box">
      <strong>{e ? e.problem?.title ?? "Request failed" : "Something went wrong"}</strong>
      <div>{e ? e.message : String(error)}</div>
      {e?.problem?.request_id && <div className="muted">request id: {e.problem.request_id}</div>}
    </div>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
