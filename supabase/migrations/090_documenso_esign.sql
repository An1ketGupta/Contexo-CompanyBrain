-- ── 090: Documenso e-signing — replaces the in-house PyMuPDF stamper ────────
--
-- Why this migration exists
-- -------------------------
-- The in-house signer (apps/esign, provider='inhouse', migration 082) drew a
-- visible signature image onto the PDF with PyMuPDF and appended an audit
-- page. It had no cryptographic signature (no PAdES), routing/stamp-placement
-- bugs were ours to debug, and it was a whole signing engine to maintain.
--
-- We replaced the stamping engine with self-hosted **Documenso** (AGPL, a
-- real DocuSign alternative that produces PAdES-sealed PDFs). apps/esign is
-- kept, but repurposed into a thin *adapter*: it translates our envelope
-- concept into Documenso's v2 Envelope API, embeds Documenso's signing UI on
-- our own /sign/{token} page, and consumes Documenso's completion webhook to
-- fire the same Inngest events the onboarding agent already listens for.
--
-- Schema impact stays minimal because `onboarding_signing_envelopes` has been
-- provider-agnostic (`provider TEXT`, `signers JSONB`) since the
-- DocuSign→DocuSeal→inhouse cutovers (075/080/082). New envelopes use
-- `provider = 'documenso'`. We only add one nullable column to hold
-- Documenso's own envelope id (needed to correlate inbound webhooks and to
-- cancel), and extend the per-signer JSONB shape with the Documenso recipient
-- id + signing token used to build the embed.
--
-- Historical rows (provider in ('docuseal','docusign','inhouse')) are left
-- untouched and read-only. Dropping the dead inhouse artifacts is a separable
-- low-risk cleanup once Documenso is proven.
--
-- Idempotent — every statement is re-runnable.

ALTER TABLE onboarding_signing_envelopes
  ADD COLUMN IF NOT EXISTS documenso_envelope_id TEXT;

-- Inbound webhooks correlate on Documenso's envelope id; index it so the
-- webhook receiver's lookup is a single index hit, not a JSONB scan.
CREATE INDEX IF NOT EXISTS idx_signing_envelopes_documenso_envelope_id
  ON onboarding_signing_envelopes (documenso_envelope_id)
  WHERE documenso_envelope_id IS NOT NULL;

COMMENT ON COLUMN onboarding_signing_envelopes.documenso_envelope_id IS
  'Documenso''s own envelope id (provider=''documenso''). NULL for historical '
  'inhouse/docuseal/docusign envelopes. Set by apps/esign on create; used to '
  'correlate the DOCUMENT_* completion webhook back to this row and to cancel '
  'the envelope in Documenso when an onboarding run is voided.';

COMMENT ON COLUMN onboarding_signing_envelopes.signers IS
  'Per-signer state for routed envelopes. Array of '
  '{role, email, name, routing_order, status, completed_at, public_token, '
  'public_token_expires_at, documenso_recipient_id, documenso_token, '
  'ip_address, user_agent, consent_accepted_at}. For provider=''documenso'' '
  'the signing UI is Documenso''s, embedded on our /sign/{public_token} page '
  'via documenso_token; PDF sealing + audit trail are handled by Documenso. '
  'Historical DocuSeal-era entries used {recipient_id, client_user_id, '
  'signing_url, ...} and inhouse entries used {public_token, ip_address, '
  'consent_accepted_at, ...}; both are read-only.';

COMMENT ON COLUMN onboarding_signing_envelopes.provider IS
  'E-sign provider that issued this envelope. Today: documenso (default going '
  'forward, migration 090). Historical rows may have provider=inhouse '
  '(migration 082), docuseal, or docusign; those envelopes are read-only.';
