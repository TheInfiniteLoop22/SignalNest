# Design Q&A

Questions a reader is likely to ask about how SignalNest works, with short answers. Longer
reasoning is in [DECISIONS.md](DECISIONS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Queue and concurrency

**How do two workers avoid probing the same monitor?**
`FOR UPDATE SKIP LOCKED` in the claim query. A locked row is skipped rather than waited on, so
each worker receives a disjoint set of due monitors.

**What happens if a worker dies mid-probe?**
Nothing was written yet. The monitor's `locked_until` lease expires and another worker claims it.

**Why two short transactions around the probe?**
So no database connection or row lock is held while waiting on a remote server.

**What is the downside of a lease?**
If a probe outlived its lease the monitor could be probed twice. The lease is
`2 x timeout + 15 s` and a probe is bounded by its own timeout, so this does not happen in
practice. The worst case is one extra run row.

**Why `SELECT ... FOR UPDATE` on the monitor when recording a result?**
It serialises completions for the same monitor and makes a concurrent delete wait for the
transaction instead of racing it.

## Security

**What is SSRF and how is it prevented?**
The service fetches URLs that users supply, so it could be pointed at internal addresses such as
cloud metadata (`169.254.169.254`). Every resolved address must be public, and the connection is
pinned to the validated IP. See [ARCHITECTURE.md](ARCHITECTURE.md#ssrf-guard).

**What is DNS rebinding?**
A name that resolves to a public IP when validated and to a private one when connected. Pinning
removes the second lookup, and the check runs on every probe.

**Why 404 and not 403 for another user's monitor?**
A 403 confirms that the resource exists.

## Networking

**What does each timing measure?**
DNS is the name lookup, connect is the TCP handshake, TLS is the handshake, and first byte is the
time from sending the request to receiving response headers.

**Connection refused versus timeout?**
Refused means the host answered with a reset because nothing listens on that port. A timeout
means no answer at all, for example a firewall drop or a dead host.

**Why are redirects not followed?**
A redirect target is another URL that would need the full SSRF check again, and a redirect
already shows the service is reachable.

## Design

**Why wait for N failures before opening an incident?**
It debounces a single dropped packet. The cost is detecting a real outage N-1 probes later.

**Why is the incident rule a pure function?**
It can be tested exhaustively with no database.

**What would change at 100x scale?**
Batch result inserts, partition `check_runs` by time and add a retention job, and run several
worker processes against the same table.
