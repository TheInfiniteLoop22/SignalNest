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
      <strong>{e ? "Request failed" : "Something went wrong"}</strong>
      <div>{e ? e.message : String(error)}</div>
    </div>
  );
}
