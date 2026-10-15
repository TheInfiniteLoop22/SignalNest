"""Plain-English explanation for each failure class (shown on the run detail screen)."""

_EXPLANATIONS = {
    "INVALID_TARGET": "The monitor's URL is not valid, so no request was made.",
    "DNS_FAILURE": "The hostname could not be resolved: the name does not exist or has no address.",
    "DNS_TIMEOUT": "The DNS resolver did not answer in time.",
    "BLOCKED_BY_POLICY": (
        "The target resolves to an address SignalNest refuses to contact (private, loopback, "
        "link-local or otherwise not public). No request was sent."
    ),
    "CONNECTION_REFUSED": (
        "The host answered the connection attempt with a reset: nothing is listening on that port."
    ),
    "CONNECT_TIMEOUT": (
        "No reply to the connection attempt within the timeout — the host may be down or "
        "packets are being dropped."
    ),
    "CONNECTION_RESET": (
        "The connection was established but then closed or reset before a response arrived."
    ),
    "TLS_CERT_INVALID": (
        "The TLS certificate failed verification (expired, untrusted issuer or wrong hostname)."
    ),
    "TLS_ERROR": "The TLS handshake failed for a reason other than certificate verification.",
    "READ_TIMEOUT": (
        "The connection (and TLS, if used) succeeded, but the server did not send response "
        "headers within the timeout."
    ),
    "TOTAL_TIMEOUT": (
        "The server started responding, but the whole response did not finish within the timeout."
    ),
    "HTTP_UNEXPECTED_STATUS": "The server answered with an HTTP status that is not allowed.",
    "ASSERTION_FAILED": "The endpoint answered, but at least one assertion did not hold.",
    "PROBE_INTERNAL_ERROR": (
        "SignalNest itself hit an unexpected error. This says nothing about the target and is "
        "excluded from incidents and uptime."
    ),
}


def explain(failure_class: str | None, outcome: str) -> str:
    if failure_class is None:
        return {
            "PASS": "The endpoint answered and every check passed.",
            "DEGRADED": "The endpoint answered correctly but slower than the degraded threshold.",
        }.get(outcome, "")
    return _EXPLANATIONS.get(failure_class, "")
