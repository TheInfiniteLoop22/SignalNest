from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import (
    CurrentUser,
    Prober,
    enforce,
    get_policy,
    get_prober,
    get_resolver,
    limiter,
    require_csrf_header,
)
from app.api.monitors import check_target
from app.api.ratelimit import FixedWindowLimiter
from app.api.schemas import ProbeTestIn, ProbeTestOut, Timings
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, Resolver
from app.services.explain import explain

router = APIRouter(prefix="/probe", tags=["probe"], dependencies=[Depends(require_csrf_header)])

TestLimiter = Annotated[FixedWindowLimiter, Depends(limiter("probe_test"))]


@router.post("/test", response_model=ProbeTestOut)
async def probe_test(
    body: ProbeTestIn,
    user: CurrentUser,
    rate: TestLimiter,
    policy: Annotated[SsrfPolicy, Depends(get_policy)],
    resolver: Annotated[Resolver, Depends(get_resolver)],
    prober: Annotated[Prober, Depends(get_prober)],
) -> ProbeTestOut:
    """One unsaved probe. The only place the API process makes outbound requests."""
    enforce(rate, str(user.id))
    await check_target(body.url, policy, resolver)  # 422 blocked-target before any packet
    result = await prober(
        ProbeRequest(
            url=body.url,
            method=body.method,
            timeout_ms=body.timeout_ms,
            headers=body.headers,
            assertions=body.assertions,
            degraded_ms=body.degraded_ms,
        )
    )
    final = result.final
    return ProbeTestOut(
        outcome=result.outcome.value,
        failure_class=result.failure_class.value if result.failure_class else None,
        explanation=explain(
            result.failure_class.value if result.failure_class else None, result.outcome.value
        ),
        timings=Timings(
            dns_ms=final.dns_ms,
            connect_ms=final.connect_ms,
            tls_ms=final.tls_ms,
            ttfb_ms=final.ttfb_ms,
            total_ms=final.total_ms,
        ),
        resolved_ip=final.resolved_ip,
        http_status=final.http_status,
        attempt_count=len(result.attempts),
        assertion_results=[
            {"type": r.type, "expected": r.expected, "actual": r.actual, "passed": r.passed}
            for r in result.assertion_results
        ],
        response_headers=result.response_headers,
        response_snippet=result.response_snippet,
        body_truncated=result.body_truncated,
        error_detail=result.error_detail,
    )
