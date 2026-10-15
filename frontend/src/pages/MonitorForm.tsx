import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useMonitor, useProbeTest, useSaveMonitor } from "../api/hooks";
import { Waterfall } from "../components/Waterfall";
import { OutcomeBadge } from "../components/Badge";
import { ErrorBox, Loading } from "../components/Common";
import {
  FORM_DEFAULTS,
  fromMonitor,
  monitorFormSchema,
  toMonitorInput,
  type MonitorFormParsed,
  type MonitorFormValues,
} from "../lib/monitorForm";

function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && !error && <span className="muted small">{hint}</span>}
      {error && (
        <span role="alert" className="field-error">
          {error}
        </span>
      )}
    </label>
  );
}

export function MonitorFormPage() {
  const { id } = useParams();
  const editing = id !== undefined;
  const existing = useMonitor(id ?? "");
  if (editing && existing.isLoading) return <Loading />;
  if (editing && existing.error) return <ErrorBox error={existing.error} />;
  const initial = editing && existing.data ? fromMonitor(existing.data) : FORM_DEFAULTS;
  return <FormInner id={id} initial={initial} />;
}

function FormInner({ id, initial }: { id: string | undefined; initial: MonitorFormValues }) {
  const navigate = useNavigate();
  const save = useSaveMonitor(id);
  const probe = useProbeTest();
  const {
    register,
    handleSubmit,
    getValues,
    setError,
    trigger,
    formState: { errors, isSubmitting },
  } = useForm<MonitorFormValues, unknown, MonitorFormParsed>({
    resolver: zodResolver(monitorFormSchema),
    defaultValues: initial,
  });

  async function onSubmit(values: MonitorFormParsed) {
    try {
      const saved = await save.mutateAsync(toMonitorInput(values));
      navigate(`/monitors/${saved.id}`);
    } catch (e) {
      if (e instanceof ApiError) {
        // Map server-side field errors back onto the form; fall back to the URL field for
        // policy rejections (blocked-target / invalid-target), which are about the URL.
        const fields = e.fieldErrors;
        let placed = false;
        for (const [field, message] of Object.entries(fields)) {
          if (field in FORM_DEFAULTS) {
            setError(field as keyof MonitorFormValues, { message });
            placed = true;
          }
        }
        if (!placed && /blocked-target|invalid-target/.test(e.problem?.type ?? "")) {
          setError("url", { message: e.message });
        } else if (!placed && e.status === 409) {
          setError("name", { message: e.message });
        }
      }
    }
  }

  async function testRequest() {
    if (!(await trigger())) return;
    const parsed = monitorFormSchema.parse(getValues());
    const input = toMonitorInput(parsed);
    probe.mutate({
      url: input.url,
      method: input.method,
      timeout_ms: Math.min(input.timeout_ms, 10_000),
      headers: input.headers,
      assertions: input.assertions,
      degraded_ms: input.degraded_ms,
    });
  }

  const serverError = save.error instanceof ApiError ? save.error : null;
  const showGenericError =
    serverError && Object.keys(serverError.fieldErrors).length === 0 && ![409, 422].includes(serverError.status);

  return (
    <>
      <h1>{id ? "Edit monitor" : "New monitor"}</h1>
      <form onSubmit={(e) => void handleSubmit(onSubmit)(e)} noValidate className="form">
        <Field label="Name" error={errors.name?.message}>
          <input {...register("name")} />
        </Field>
        <Field label="URL" error={errors.url?.message} hint="http or https; private and loopback addresses are refused">
          <input {...register("url")} placeholder="https://api.example.com/health" />
        </Field>
        <div className="row">
          <Field label="Method" error={errors.method?.message}>
            <select {...register("method")}>
              <option>GET</option>
              <option>HEAD</option>
            </select>
          </Field>
          <Field label="Interval (s)" error={errors.interval_seconds?.message}>
            <input type="number" {...register("interval_seconds")} />
          </Field>
          <Field label="Timeout (ms)" error={errors.timeout_ms?.message}>
            <input type="number" {...register("timeout_ms")} />
          </Field>
          <Field label="Degraded above (ms)" error={errors.degraded_ms?.message} hint="optional">
            <input type="number" {...register("degraded_ms")} />
          </Field>
        </div>
        <div className="row">
          <Field label="Failures to open incident" error={errors.failure_threshold?.message}>
            <input type="number" {...register("failure_threshold")} />
          </Field>
          <Field label="Successes to resolve" error={errors.recovery_threshold?.message}>
            <input type="number" {...register("recovery_threshold")} />
          </Field>
        </div>

        <fieldset>
          <legend>Assertions (optional)</legend>
          <Field label="Allowed status codes" error={errors.status_values?.message} hint="e.g. 200, 204. Default: 200-399">
            <input {...register("status_values")} />
          </Field>
          <Field label="Respond faster than (ms)" error={errors.max_latency_ms?.message}>
            <input type="number" {...register("max_latency_ms")} />
          </Field>
          <Field label="Body contains" error={errors.body_contains?.message}>
            <input {...register("body_contains")} />
          </Field>
          <div className="row">
            <Field label="JSON path" error={errors.json_path?.message} hint="e.g. status or items.0.id">
              <input {...register("json_path")} />
            </Field>
            <Field label="Must equal" error={errors.json_value?.message}>
              <input {...register("json_value")} />
            </Field>
          </div>
        </fieldset>

        <Field label="Headers" error={errors.headers_text?.message} hint="One per line: Name: value (no Authorization or Cookie)">
          <textarea rows={3} {...register("headers_text")} />
        </Field>

        {showGenericError && <ErrorBox error={serverError} />}
        {serverError && !showGenericError && Object.keys(serverError.fieldErrors).length === 0 && (
          <p role="alert" className="field-error">
            {serverError.message}
          </p>
        )}

        <div className="actions">
          <button type="submit" disabled={isSubmitting || save.isPending}>
            {id ? "Save changes" : "Create monitor"}
          </button>
          <button type="button" className="secondary" onClick={() => void testRequest()} disabled={probe.isPending}>
            {probe.isPending ? "Testing…" : "Test request"}
          </button>
          <Link to={id ? `/monitors/${id}` : "/"}>Cancel</Link>
        </div>
      </form>

      {probe.error && <ErrorBox error={probe.error} />}
      {probe.data && (
        <section className="card" aria-live="polite" data-testid="probe-result">
          <h2>
            Test result <OutcomeBadge outcome={probe.data.outcome} />
          </h2>
          <p>{probe.data.explanation}</p>
          <p className="muted">
            {probe.data.http_status !== null && <>HTTP {probe.data.http_status} · </>}
            {probe.data.resolved_ip && <>{probe.data.resolved_ip} · </>}
            {probe.data.attempt_count} attempt{probe.data.attempt_count === 1 ? "" : "s"} · not saved
          </p>
          <Waterfall timings={probe.data.timings} />
        </section>
      )}
    </>
  );
}
