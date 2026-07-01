# apps/esign — in-house e-signing service

Replaces DocuSeal for the Onboarding v2 LOI / Appointment Letter+NDA signing
flow. Self-contained FastAPI service at `apps/esign/` in this repo, deployed
as its own Render Web Service (no committed `render.yaml` — provisioned via
the Render dashboard, same as DocuSeal was).

Why we built our own instead of using DocuSeal/OpenSign/Documenso: every
free/self-hosted option we evaluated gated the file-ingest or API surface
behind a paid tier, which forced an awkward per-org "build a template in a
second admin panel" step. apps/esign signs the already-rendered PDF our own
pipeline produces directly — no external admin UI, no per-org setup, no
webhook/HMAC verification (it shares Supabase + Inngest credentials with
apps/api and writes state directly).

Cost: $0 — no per-document fee, no Pro license. Just Render hosting (free
tier is fine at low volume; same cold-start caveat as DocuSeal applied here).

---

## One-time provisioning

You need: a Render account, this repo pushed to GitHub, and the same
Supabase project + Inngest account apps/api already uses.

1. **Render dashboard → New → Web Service** → connect this repo.
2. **Root Directory**: `apps/esign`
3. **Runtime**: Docker (uses `apps/esign/Dockerfile`)
4. **Instance type**: Free (or Starter if you want to avoid cold starts)
5. **Environment variables** — see `apps/esign/.env.example`:
   ```
   SUPABASE_URL=<same as apps/api>
   SUPABASE_SERVICE_ROLE_KEY=<same as apps/api>
   INNGEST_EVENT_KEY=<same Inngest account as apps/api>
   ESIGN_API_KEY=<generate a long random string>
   APP_URL=https://<your Vercel domain>
   ENVIRONMENT=production
   ```
6. Deploy. Confirm `https://<your-service>.onrender.com/health` returns
   `{"status": "ok", ...}`.

### Wire the other two apps

**apps/api** (Railway env vars):
```
ESIGN_SERVICE_URL=https://<your-service>.onrender.com
ESIGN_API_KEY=<same value as step 5 above>
```

**apps/web** (Vercel env vars):
```
ESIGN_API_URL=https://<your-service>.onrender.com
```

Redeploy both. The onboarding LOI/offer-bundle flow now signs through
apps/esign automatically; missing env vars disable the integration cleanly
(HR sees the print/scan/email fallback, same as when DocuSeal wasn't
configured).

### Database

Run `supabase db push` to apply migration 082 (adds a GIN index on
`onboarding_signing_envelopes.signers` for the public-token lookup — no
destructive changes, safe to re-run).

---

## Local development

```bash
cd apps/esign
uv sync
cp .env.example .env   # fill in SUPABASE_*, INNGEST_EVENT_KEY, ESIGN_API_KEY, APP_URL=http://localhost:3000
uv run uvicorn app.main:app --reload --port 8001
```

Then in `apps/api/.env`:
```
ESIGN_SERVICE_URL=http://localhost:8001
ESIGN_API_KEY=<same value as apps/esign's .env>
```

And in `apps/web/.env.local`:
```
ESIGN_API_URL=http://localhost:8001
```

No tunnel needed for local dev, unlike DocuSeal's webhook setup — apps/esign
writes Supabase directly and calls Inngest's HTTP API, both of which are
reachable from your laptop without exposing your own machine.

---

## Operational notes

### Signing depth (current scope)

Visible signature (drawn or typed) stamped onto the PDF via PyMuPDF at a
`{{ hr_signature_block }}` / `{{ candidate_signature_block }}` marker
position (falls back to bottom-of-last-page if the template has no
marker), plus an appended audit-certificate page (signer identity, IP,
user agent, consent timestamp, document SHA-256). This is the same tier of
signing DocuSeal/HelloSign's default plans use — legally sufficient for
LOI/offer-letter-class documents, not a PKI-qualified signature.

A future upgrade path exists if a stronger tamper-evidence guarantee is
ever needed: real PAdES cryptographic signing via `pyhanko` + a
self-managed certificate. Not implemented — `apps/esign/app/pdf_sign.py`
is where that would go.

### Monitoring

- Logs: Render dashboard → your service → Logs.
- Every envelope's lifecycle is queryable in Supabase:
  `onboarding_signing_envelopes.events` (append-only, capped implicitly by
  what apps/esign writes — no 50-entry cap like the old DocuSeal webhook
  ingest had, since there's no external retry storm to bound here).

### Rotating the shared API key

```
# Generate a new value, then update BOTH:
#   Render: ESIGN_API_KEY
#   Railway (apps/api): ESIGN_API_KEY
# Redeploy both in the same window so no in-flight call fails auth.
```
