"""Tell the outside world about an incident: one POST to the monitor's webhook URL.

Best effort: we try once and log the outcome. The request goes through the probe engine, so
webhook URLs get the same SSRF protection as monitored URLs.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.db.models import Monitor
from app.incidents.state_machine import Action
from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, ProbeResult, Resolver

log = logging.getLogger("signalnest.webhook")


def build_message(monitor: Monitor, action: Action, result: ProbeResult) -> dict[str, object]:
    opened = action is Action.OPEN
    reason = result.failure_class.value if result.failure_class else "ERROR"
    text = (
        f"SignalNest: {monitor.name} is DOWN ({reason})"
        if opened
        else f"SignalNest: {monitor.name} has recovered"
    )
    return {
        "text": text,  # Slack reads "text"
        "content": text,  # Discord reads "content"
        "event": "incident.opened" if opened else "incident.resolved",
        "monitor": {"id": str(monitor.id), "name": monitor.name, "url": monitor.url},
        "at": datetime.now(UTC).isoformat(),
    }


async def notify(
    monitor: Monitor,
    action: Action,
    result: ProbeResult,
    *,
    policy: SsrfPolicy,
    resolver: Resolver | None = None,
    transport: Any = None,
) -> bool:
    """POST the message once. Returns True if the webhook accepted it."""
    assert monitor.webhook_url
    sent = await run_probe(
        ProbeRequest(
            url=monitor.webhook_url,
            timeout_ms=5000,
            method="POST",
            headers=(("Content-Type", "application/json"),),
            body=json.dumps(build_message(monitor, action, result)).encode(),
        ),
        policy=policy,
        resolver=resolver,
        transport=transport,
    )
    ok = sent.failure_class is None
    log.info("webhook monitor=%s action=%s delivered=%s", monitor.id, action.value, ok)
    return ok
