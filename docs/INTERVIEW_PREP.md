# Interview prep

## The 60-second version

> SignalNest is an uptime monitor. Users add URLs; a worker probes them on a schedule and stores
> the DNS, connect, TLS and first-byte timings of every probe. After N consecutive failures it
> opens an incident, and the first pass resolves it. Two parts were interesting: using Postgres as
> the job queue with `SKIP LOCKED` leases so multiple workers can run safely, and making the
> outbound probing SSRF-safe, since the service fetches URLs that users type in.

## Questions to be ready for

**Queue and concurrency**
1. How do two workers avoid probing the same monitor? *`FOR UPDATE SKIP LOCKED` in the claim
   query: a locked row is skipped, not waited on, so each worker gets a disjoint set.*
2. What if a worker dies mid-probe? *Nothing was written. `locked_until` expires and another
   worker claims the monitor.*
3. Why two short transactions around the probe? *So no connection or row lock is held while
   waiting on a remote server.*
4. What is the downside of a lease? *If a probe outlives its lease, the monitor can be probed
   twice. The lease is `2 x timeout + 15 s`, and a probe is bounded by its timeout.*
5. Why `SELECT ... FOR UPDATE` on the monitor in `record_result`? *It serialises completions for
   the same monitor and makes a concurrent delete wait.*

**Security**
6. What is SSRF and how is it prevented here? *See `docs/ARCHITECTURE.md`: validate URL, resolve,
   check every IP, connect to the pinned IP.*
7. What is DNS rebinding? *A name that resolves to a public IP at validation time and a private
   one at connect time. Pinning removes the second lookup.*
8. Why check again on every probe and not only when the monitor is created? *DNS changes.*
9. Why 404 and not 403 for another user's monitor? *403 confirms the resource exists.*

**Networking**
10. What does each timing measure? *DNS: name lookup. Connect: TCP handshake. TLS: handshake.
    First byte: time from sending the request to response headers.*
11. Connection refused vs timeout? *Refused: the host answered with a reset, nothing listens.
    Timeout: no answer at all, a firewall or a dead host.*
12. Why not follow redirects? *A redirect target is another URL that would need the whole SSRF
    check again, and a redirect already proves the service is reachable.*

**Design**
13. Why wait for N failures before an incident? *Debouncing a single blip. The cost is detection
    delay of N-1 probes.*
14. Why is the state machine a pure function? *Easy to test exhaustively with no database.*
15. What would you change at 100x scale? *Batch result inserts, partition `check_runs` by time and
    add a retention job, and put several worker processes behind the same table.*

## What this project does not do (say it before they ask)

No notifications, one vantage point, GET only, no redirect following, no data retention, not
deployed. The earlier full-featured version, with retries, assertions, hints and deployment
assets, is the `v1.0.0` tag.
