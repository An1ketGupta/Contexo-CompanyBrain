-- ── 082: In-house e-signing — replaces DocuSeal ────────────────────────────
--
-- Why this migration exists
-- -------------------------
-- Self-hosted DocuSeal (free tier) blocked every file-ingest API endpoint
-- behind a Pro license, forcing HR to hand-build a matching template in
-- DocuSeal's own UI per org (migration 081). We replaced DocuSeal with our
-- own signing service, apps/esign — a separate FastAPI app (deployed on
-- Render) that signs the already-rendered onboarding PDF directly, no
-- per-org template step.
--
-- Schema impact is minimal because `onboarding_signing_envelopes` was
-- already provider-agnostic (`provider TEXT`, `signers JSONB`) from the
-- DocuSign→DocuSeal cutover (migrations 075/080). New envelopes just use
-- `provider = 'inhouse'`. The one new thing apps/esign needs is a
-- per-signer public signing token — stored as `public_token` /
-- `public_token_expires_at` fields inside each `signers[]` entry (JSONB,
-- no column needed) so the public /sign/{token} route can look an envelope
-- up by token via containment (`signers @> [{"public_token": "..."}]`).
-- This index makes that lookup fast.
--
-- `onboarding_docuseal_templates` (081) is left in place — dead once the
-- code stops reading it, but dropping it is a separable, low-risk cleanup
-- once the new system is proven in production.
--
-- Idempotent — every statement re-runnable.

CREATE INDEX IF NOT EXISTS idx_signing_envelopes_signers_gin
  ON onboarding_signing_envelopes USING GIN (signers jsonb_path_ops);

COMMENT ON COLUMN onboarding_signing_envelopes.signers IS
  'Per-signer state for routed envelopes. Array of '
  '{role, email, name, routing_order, status, completed_at, '
  'public_token, public_token_expires_at, ip_address, user_agent, '
  'consent_accepted_at}. Written directly by apps/esign (provider=''inhouse'') '
  'via a shared Supabase service-role key — no webhook hop. Historical '
  'DocuSeal-era entries (provider=''docuseal'') used a different shape '
  '{recipient_id, client_user_id, signing_url, declined_at, decline_reason} '
  'and are read-only.';

COMMENT ON COLUMN onboarding_signing_envelopes.provider IS
  'E-sign provider that issued this envelope. Today: inhouse (apps/esign, '
  'default going forward). Historical rows may have provider=docuseal or '
  'provider=docusign; those envelopes are read-only.';
