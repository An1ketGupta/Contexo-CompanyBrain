# DocuSeal — self-hosted e-signing service

Replaces DocuSign for the Onboarding v2 LOI signing flow. Runs on Fly.io
as a single-region Mumbai (`bom`) service backed by Fly Postgres and a
persistent volume for uploaded templates / signed PDFs.

Public URL: `https://sign.nirnayaiq.com` (CNAME → `nirnayaiq-docuseal.fly.dev`).

Cost: ~$5/mo app + ~$2/mo Postgres + sub-$1/mo bandwidth = **~$8/mo total**,
replacing the $40+/mo DocuSign Business Pro tier with no envelope cap.

---

## One-time provisioning

You need: `flyctl` installed (`iwr https://fly.io/install.ps1 -useb | iex` on
PowerShell), a Fly.io account with billing attached, Cloudflare access to
the `nirnayaiq.com` DNS, and a Resend API key for outbound SMTP.

Run each block in order. Every command is idempotent or will surface a
clear "already exists" error you can ignore.

```powershell
# 1. Create the Fly app (links this directory's fly.toml).
fly apps create nirnayaiq-docuseal

# 2. Create the Postgres cluster — small shared-cpu-1x is fine at our scale.
fly postgres create `
  --name nirnayaiq-docuseal-db `
  --region bom `
  --vm-size shared-cpu-1x `
  --volume-size 3 `
  --initial-cluster-size 1

# 3. Attach Postgres → DocuSeal (sets DATABASE_URL automatically).
fly postgres attach nirnayaiq-docuseal-db --app nirnayaiq-docuseal

# 4. Create the persistent volume for /data (templates, attachments, audit logs).
fly volumes create docuseal_data --size 5 --region bom --app nirnayaiq-docuseal --yes

# 5. Generate + set the four secrets DocuSeal needs at boot.
$secretKey   = -join ((1..128) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
$webhookSec  = -join ((1..64)  | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })

fly secrets set --app nirnayaiq-docuseal `
  SECRET_KEY_BASE=$secretKey `
  WEBHOOK_URL=https://api.nirnayaiq.com/docuseal/webhook `
  WEBHOOK_SECRET=$webhookSec `
  SMTP_ADDRESS=smtp.resend.com `
  SMTP_PORT=465 `
  SMTP_USERNAME=resend `
  SMTP_PASSWORD=<paste-resend-api-key-here> `
  SMTP_DOMAIN=nirnayaiq.com `
  SMTP_AUTHENTICATION=plain `
  SMTP_TLS=true

# Save $webhookSec — you'll set the same value as DOCUSEAL_WEBHOOK_SECRET
# on the FastAPI side so the inbound /docuseal/webhook can verify HMAC.

# 6. First deploy.
fly deploy --config infra/docuseal/fly.toml --app nirnayaiq-docuseal

# 7. Wait for the app to come up healthy.
fly status --app nirnayaiq-docuseal
```

### DNS + TLS

Cloudflare → `nirnayaiq.com` zone → DNS:

- Type: `CNAME`
- Name: `sign`
- Target: `nirnayaiq-docuseal.fly.dev`
- **Proxy status: DNS only (grey cloud, NOT orange)**

The orange-cloud proxy puts Cloudflare's certificate in front of Fly's,
which breaks the cert chain DocuSeal expects. Once DNS resolves:

```powershell
fly certs create sign.nirnayaiq.com --app nirnayaiq-docuseal
fly certs show sign.nirnayaiq.com --app nirnayaiq-docuseal  # wait for "Issued"
```

### One-time DocuSeal admin setup (5 min)

1. Visit `https://sign.nirnayaiq.com` once DNS + cert are live.
2. Complete the admin sign-up form. Email = the platform owner (you).
3. **Settings → API → Create token**. Copy the value.
4. **Settings → Webhooks**. Confirm the URL pre-populated from `WEBHOOK_URL`
   is `https://api.nirnayaiq.com/docuseal/webhook`. Enable all eight event
   types (`form.*` and `submission.*`).
5. **Settings → Account → From name** = `NirnayaIQ Onboarding`.
6. **Settings → Branding → "Powered by DocuSeal"** stays on (AGPLv3 §7(b)
   requires the attribution — do not strip it).

### Wire FastAPI side

Set three secrets on Railway (or the dev `.env`):

```
DOCUSEAL_BASE_URL=https://sign.nirnayaiq.com
DOCUSEAL_API_KEY=<paste-token-from-step-3>
DOCUSEAL_WEBHOOK_SECRET=<paste-the-$webhookSec-from-fly-setup>
```

Redeploy the API. The LOI flow now uses DocuSeal automatically; missing
secrets disable the integration cleanly (HR still sees a fallback to
print/scan).

---

## Local development

Two options.

### Option A — proxy the staging instance into localhost

Cheapest for routine dev. No DocuSeal install needed locally.

```powershell
fly proxy 13000:3000 -a nirnayaiq-docuseal
# In another shell: set DOCUSEAL_BASE_URL=http://localhost:13000 in apps/api/.env
```

To receive webhook callbacks on your local FastAPI, expose `localhost:8000`
via a tunnel and temporarily set DocuSeal's `WEBHOOK_URL` to the tunnel URL.

```powershell
cloudflared tunnel --url http://localhost:8000
# Paste the printed *.trycloudflare.com URL into DocuSeal Settings → Webhooks.
# Remember to switch it back to https://api.nirnayaiq.com/docuseal/webhook
# when you stop developing — DocuSeal will retry events indefinitely.
```

### Option B — run DocuSeal locally in Docker

For deeper changes (e.g. inspecting webhook payload shape during a bug
hunt).

```powershell
docker run -d --name docuseal `
  -p 3500:3000 `
  -e SECRET_KEY_BASE=dev-only-not-secure-keep-out-of-prod `
  -e HOST=localhost:3500 `
  -e WEBHOOK_URL=https://<your-tunnel>.trycloudflare.com/docuseal/webhook `
  -e WEBHOOK_SECRET=dev-shared-secret `
  -v docuseal_data:/data `
  docuseal/docuseal:1.10

# apps/api/.env:
# DOCUSEAL_BASE_URL=http://localhost:3500
# DOCUSEAL_API_KEY=<from local admin UI>
# DOCUSEAL_WEBHOOK_SECRET=dev-shared-secret
```

---

## Operational notes

### Backups

- Fly Postgres has automatic daily snapshots on the managed cluster.
- The `docuseal_data` volume: schedule a daily snapshot via Fly Machines:
  ```powershell
  fly volumes snapshots create docuseal_data --app nirnayaiq-docuseal
  ```
  Set a recurring CI job (GitHub Actions cron, `0 3 * * *` UTC) to do this.
- Supabase Storage already mirrors every completed signing as
  `orgs/{org}/onboarding/{run}/loi_docuseal_signed.pdf`. DocuSeal is
  therefore not the sole source of truth for any signed artifact — losing
  the Fly volume costs only in-flight envelopes.

### Logs + monitoring

- App logs: `fly logs -a nirnayaiq-docuseal` (or the Fly dashboard).
- Webhook deliveries: visible in DocuSeal admin → Webhooks → "History".
  Failed deliveries retry with exponential backoff.
- FastAPI side: every webhook event is appended to
  `onboarding_signing_envelopes.events` (capped at 50 most recent), so the
  full delivery trail is queryable in Supabase.

### Upgrading DocuSeal

```powershell
# 1. Bump the image tag in infra/docuseal/fly.toml.
# 2. Read the changelog: https://github.com/docusealco/docuseal/releases
# 3. Deploy.
fly deploy --config infra/docuseal/fly.toml --app nirnayaiq-docuseal
```

Rails migrations run automatically at boot. If a release notes a breaking
change to the webhook payload shape, update
`apps/api/app/services/integrations/docuseal/client.py:ingest_webhook_event`
first, ship it, *then* upgrade.

### Rotating secrets

```powershell
# API key (compromise scenario):
# DocuSeal admin → API → revoke + create new token.
# Update Railway: DOCUSEAL_API_KEY=<new>, redeploy.

# Webhook secret:
$new = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
fly secrets set WEBHOOK_SECRET=$new --app nirnayaiq-docuseal
# Update Railway: DOCUSEAL_WEBHOOK_SECRET=$new, redeploy in the same window
# so no inbound webhook fails verification.
```

### License compliance (AGPLv3)

We run DocuSeal unmodified. The AGPL §7(b) clause requires:

1. The "Powered by DocuSeal" footer in the embedded signing UI must stay
   visible — do NOT theme it out.
2. Anyone who interacts with the service (candidates signing LOIs) is
   entitled to the unmodified source we deploy. Link to the upstream
   release tag at `https://github.com/docusealco/docuseal/releases/tag/v1.10`
   from your privacy / open-source-attributions page.

These are the only obligations because we don't modify the source. If we
ever fork, the AGPL requires we publish the fork under the same license.
