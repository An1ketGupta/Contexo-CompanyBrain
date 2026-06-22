# Tech Debt — Post-Launch Hardening Backlog

Items deferred from the 14-day Production Readiness sprint. Each entry
includes the trigger (when this becomes urgent) so we don't relitigate
priorities every planning cycle.

---

## Security

### SSRF — DNS rebinding gap
**Where:** `apps/api/app/services/network_security.py`
**What it does today:** Resolves the hostname via `getaddrinfo`, validates
all returned A/AAAA records against the private/reserved blocklist,
returns. The actual httpx call then performs its own resolution at
TCP-connect time.
**The gap:** Between our resolution and httpx's resolution (typically
0–50ms), an attacker controlling the authoritative DNS for their domain
can flip the A record from a public IP to a private IP. httpx then
connects to the private IP. Our blocklist never sees it.
**Mitigations already in place:**
- We validate ALL records returned by `getaddrinfo`, so a round-robin
  rebind that returns both a public and a private record fails immediately.
- Webhook delivery re-validates on every retry — the rebind window is
  per-attempt, not per-webhook-lifetime.
- Eager validation at the public-API boundary catches static IP literals
  (e.g. `http://169.254.169.254`) before they ever reach the worker.
**The fix:** Pin the resolved IP and pass it directly to httpx, while
preserving the original hostname for SNI/Host header. Either:
  1. Custom `httpx.AsyncHTTPTransport` that resolves once via our
     validator and passes the IP through socket connection, or
  2. Pre-resolve, use the IP as the URL host, set `headers["Host"]`
     and `extensions={"sni_hostname": ...}` on the request.
**Trigger to prioritise:** First customer report or pentest finding
involving outbound webhook abuse, OR any SOC 2 readiness work.

---

## Observability

### JWT / session caching
**Where:** every router using `Depends(verify_jwt)`.
**What it does today:** Calls Supabase Auth on every request to resolve
JWT → user record. Adds ~30–80ms per request.
**Trigger to prioritise:** Sustained traffic > 100 concurrent users OR
p95 chat latency budget breached.

### Connection pooling
**Where:** `apps/api/app/database.py` — `get_user_client()` creates a new
client per request.
**Trigger to prioritise:** Sustained traffic > 100 concurrent users OR
Supabase connection-limit errors in Sentry.

---

## Cleanup

### Soft-deleted chunk purge
**Where:** `chunks.is_archived = true` rows accumulate forever — search
RPCs exclude them, but storage grows unboundedly.
**The fix:** Inngest cron, weekly, hard-deletes chunks where
`is_archived = true AND archived_at < now() - interval '90 days'`.
**Trigger to prioritise:** First org whose chunks table exceeds 10M rows
OR a cost alert on storage.
