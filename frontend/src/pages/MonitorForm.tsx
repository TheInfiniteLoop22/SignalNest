import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useMonitor, useSaveMonitor } from "../api/hooks";
import type { MonitorInput } from "../api/types";
import { ErrorBox, Loading } from "../components/Common";

const DEFAULTS: MonitorInput = {
  name: "",
  url: "",
  interval_seconds: 60,
  timeout_ms: 5000,
  expected_status: null,
  body_contains: null,
  failure_threshold: 3,
};

export function MonitorFormPage() {
  const { id } = useParams();
  const existing = useMonitor(id ?? "");
  if (id && existing.isLoading) return <Loading />;
  if (id && existing.error) return <ErrorBox error={existing.error} />;
  return <FormInner id={id} initial={id && existing.data ? existing.data : DEFAULTS} />;
}

function FormInner({ id, initial }: { id: string | undefined; initial: MonitorInput }) {
  const navigate = useNavigate();
  const save = useSaveMonitor(id);
  const [form, setForm] = useState<MonitorInput>({
    name: initial.name,
    url: initial.url,
    interval_seconds: initial.interval_seconds,
    timeout_ms: initial.timeout_ms,
    expected_status: initial.expected_status,
    body_contains: initial.body_contains,
    failure_threshold: initial.failure_threshold,
  });
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof MonitorInput>(key: K, value: MonitorInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const num = (v: string) => (v === "" ? null : Number(v));

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const saved = await save.mutateAsync(form);
      navigate(`/monitors/${saved.id}`);
    } catch (err) {
      // The server is the validator (blocked URL, duplicate name, interval too low...).
      setError(err instanceof ApiError ? err.message : "Could not reach the server");
    }
  }

  return (
    <>
      <h1>{id ? "Edit monitor" : "New monitor"}</h1>
      <form onSubmit={(e) => void onSubmit(e)} className="form">
        <label className="field">
          <span>Name</span>
          <input value={form.name} onChange={(e) => set("name", e.target.value)} required maxLength={100} />
        </label>
        <label className="field">
          <span>URL</span>
          <input
            value={form.url}
            onChange={(e) => set("url", e.target.value)}
            placeholder="https://api.example.com/health"
            required
          />
          <span className="muted small">http or https; private and loopback addresses are refused</span>
        </label>
        <div className="row">
          <label className="field">
            <span>Interval (s)</span>
            <input
              type="number"
              min={1}
              max={3600}
              value={form.interval_seconds}
              onChange={(e) => set("interval_seconds", Number(e.target.value))}
            />
          </label>
          <label className="field">
            <span>Timeout (ms)</span>
            <input
              type="number"
              min={1000}
              max={30000}
              value={form.timeout_ms}
              onChange={(e) => set("timeout_ms", Number(e.target.value))}
            />
          </label>
          <label className="field">
            <span>Failures before incident</span>
            <input
              type="number"
              min={1}
              max={10}
              value={form.failure_threshold}
              onChange={(e) => set("failure_threshold", Number(e.target.value))}
            />
          </label>
        </div>
        <div className="row">
          <label className="field">
            <span>Expected status</span>
            <input
              type="number"
              value={form.expected_status ?? ""}
              onChange={(e) => set("expected_status", num(e.target.value))}
            />
            <span className="muted small">optional; default is any 2xx or 3xx</span>
          </label>
          <label className="field">
            <span>Body contains</span>
            <input
              value={form.body_contains ?? ""}
              onChange={(e) => set("body_contains", e.target.value || null)}
              maxLength={200}
            />
          </label>
        </div>

        {error && (
          <p role="alert" className="field-error">
            {error}
          </p>
        )}
        <div className="actions">
          <button type="submit" disabled={save.isPending}>
            {id ? "Save changes" : "Create monitor"}
          </button>
          <Link to={id ? `/monitors/${id}` : "/"}>Cancel</Link>
        </div>
      </form>
    </>
  );
}
