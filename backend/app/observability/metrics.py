"""Prometheus metrics (docs/14 section 3).

Label discipline: labels are bounded enums or route templates. `monitor_id`, `url`, `user`
and `run_id` are deliberately NOT labels: unbounded values create unbounded time series
(cardinality explosion). Per-monitor questions are answered from the database and logs.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry()

RUNS_TOTAL = Counter(
    "signalnest_runs_total",
    "Completed check runs",
    ["outcome", "failure_class"],
    registry=registry,
)
PROBE_DURATION = Histogram(
    "signalnest_probe_duration_seconds",
    "Wall time of one probe (all attempts)",
    ["outcome"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=registry,
)
SCHEDULER_LAG = Gauge(
    "signalnest_scheduler_lag_seconds",
    "Age of the oldest due, unleased monitor (0 when nothing is waiting)",
    registry=registry,
)
IN_FLIGHT = Gauge(
    "signalnest_in_flight_probes",
    "Probes currently executing in this worker",
    ["worker_id"],
    registry=registry,
)
CLAIM_BATCH = Histogram(
    "signalnest_claim_batch_size",
    "Monitors claimed per poll that claimed anything",
    buckets=(1, 2, 5, 10, 20, 50),
    registry=registry,
)
DB_ERRORS = Counter(
    "signalnest_db_errors_total", "Database errors by operation", ["op"], registry=registry
)
HTTP_REQUESTS = Counter(
    "signalnest_http_requests_total",
    "API requests",
    ["route", "method", "status"],
    registry=registry,
)
HTTP_DURATION = Histogram(
    "signalnest_http_request_duration_seconds",
    "API request duration",
    ["route", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
    registry=registry,
)


def render() -> bytes:
    return generate_latest(registry)
