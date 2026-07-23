# NirnayaIQ E-Sign — self-hosted Documenso

This directory deploys **[Documenso](https://github.com/documenso/documenso)**
(AGPL-3.0), the open-source DocuSign alternative, as NirnayaIQ's signing
engine. It replaces the old in-house PyMuPDF stamper (`apps/esign`, migration
082), which had no cryptographic signature and was ours to debug.

Documenso produces **PAdES-sealed PDFs** with a real audit trail. NirnayaIQ
never calls it directly — `apps/esign` is now a thin **adapter** that
translates our envelope concept into Documenso's v2 API, embeds Documenso's
signer on our own `/sign/{token}` page, and consumes Documenso's completion
webhook.

```
apps/api ──create_envelope──► apps/esign (adapter) ──v2 API──► Documenso
                                    ▲                              │
apps/web /sign/[token] ─embeds─► Documenso signer (iframe)        │ webhook
                                    └──────────────────────────────┘
                                       updates DB + fires Inngest
```

## Cost

- **Documenso license:** $0 (AGPL, unlimited documents / API / webhooks self-hosted).
- **Postgres / storage / email / cert:** $0 (reuse Supabase + Resend + a self-signed cert).
- **Compute:** $0 on a free tier **with cold starts** (30–90s on first request
  after idle — the accepted tradeoff, see `render.yaml`). Remove it later by
  bumping the single service to a paid always-on instance.

## Local setup

```bash
cd services/documenso
cp .env.example .env

# 1. Secrets → paste the three lines into .env
bash scripts/generate-secrets.sh

# 2. Signing certificate → writes ./cert.p12, then set the same passphrase
#    as NEXT_PRIVATE_SIGNING_PASSPHRASE in .env
bash scripts/generate-cert.sh "some-cert-passphrase"

# 3. Fill in NEXT_PRIVATE_SMTP_PASSWORD (Resend API key), and
#    NEXT_PRIVATE_DATABASE_URL / NEXT_PRIVATE_DIRECT_DATABASE_URL with your
#    Neon connection strings (pooled + direct). No local Postgres container —
#    Documenso connects straight to Neon.

# 4. Boot
docker compose --env-file .env up -d
# open http://localhost:3030
```

### First-boot wiring (one-time)

1. Open `http://localhost:3030`, create the admin account, create a **Team**.
2. **Team → API Tokens** → create a token → set it in `apps/esign/.env`:
   ```
   DOCUMENSO_API_TOKEN=api_xxxxxxxx
   DOCUMENSO_BASE_URL=http://localhost:3030
   DOCUMENSO_PUBLIC_URL=http://localhost:3030   # what the browser embeds
   ```
3. **Team → Webhooks** → add endpoint pointing at the adapter's receiver:
   ```
   http://localhost:8001/webhooks/documenso
   ```
   subscribe to the `DOCUMENT_*` events, copy the signing secret → set in
   `apps/esign/.env`:
   ```
   DOCUMENSO_WEBHOOK_SECRET=whsec_xxxxxxxx
   ```

## Production (free tier)

- **Compute:** `render.yaml` (Render free web service, Docker image). Sleeps
  when idle. Set all `sync:false` env vars in the Render dashboard.
- **Postgres:** external free instance — [Neon](https://neon.tech) free tier or
  a second Supabase project. Paste its URL into `NEXT_PRIVATE_DATABASE_URL`.
- **Storage:** Supabase Storage exposes an S3-compatible endpoint
  (Project Settings → Storage → S3 Connection). Use a **dedicated** bucket
  (e.g. `documenso`), NOT the `document` bucket apps/api ingests into.
  Uncomment the S3 block in `.env.example`.
- **Email:** Resend SMTP relay (`smtp.resend.com`, user `resend`, password =
  a Resend API key). `FROM` must be on a Resend-verified domain.
- **Certificate:** Render has no host mount — base64-encode the p12
  (`base64 -w0 cert.p12`) into `NEXT_PRIVATE_SIGNING_LOCAL_FILE_CONTENTS`.
- Point `NEXT_PUBLIC_WEBAPP_URL` at the Render subdomain (or a custom
  `sign.nirnayaiq.com`), and update the adapter's `DOCUMENSO_*` URLs + the
  Team webhook endpoint to the deployed adapter host.

## ⚠️ Verify against the running instance

Documenso's v2 API is young and was reorganised around an **Envelope** model.
The adapter's coupling is isolated in `apps/esign/app/documenso_client.py`,
with each assumed endpoint/field documented inline. After first boot, open
`http://localhost:3030/api/v2/openapi.json` (or the Documenso API docs) and
confirm these, adjusting the client if the running version differs:

- `POST /api/v2/envelope/create` (multipart: `payload` JSON + `files`)
- `POST /api/v2/envelope/field/create-many` (SIGNATURE fields, % coordinates)
- `POST /api/v2/envelope/distribute` (`distributionMethod` to suppress
  Documenso's own emails — NirnayaIQ sends the signer emails itself)
- `GET  /api/v2/envelope/recipient/{id}` → recipient `token` for the embed
- Webhook signature header name + scheme (see `apps/esign/app/routers/webhooks.py`)

The adapter fails safe: if Documenso is unreachable or a contract check fails,
`apps/esign.is_configured()`/`create_envelope` raise and `apps/api` falls back
to the plain print/scan email flow — a broken signing deploy never strands an
onboarding run.
