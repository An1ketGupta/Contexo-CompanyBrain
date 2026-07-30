-- ── 081: Onboarding DocuSeal templates — free-tier signing path ────────────
--
-- Why this migration exists
-- -------------------------
-- Self-hosted open-source (AGPL) DocuSeal gates every *file-ingest* API
-- endpoint behind the Pro license:
--
--     POST /submissions/pdf   → 404 "available in Pro Edition"
--     POST /submissions/docx  → 404
--     POST /submissions/html  → 404
--     POST /templates/{pdf,docx,html} → 404
--
-- Our onboarding e-sign flow was built on POST /submissions/pdf (upload the
-- docxtpl→Gotenberg-rendered PDF bytes and let DocuSeal auto-place a
-- signature field). On the free edition that endpoint simply doesn't exist,
-- so the LOI/ AL+NDA signing envelopes 404 on creation.
--
-- The only *free* programmatic way to create a signable submission is
-- POST /submissions against an existing template_id — a template built once
-- in the DocuSeal web UI (uploading a PDF/DOCX via the UI is free; only the
-- API upload is Pro). Per-candidate variable data is injected as pre-filled,
-- read-only field values (submitters[].fields[{name, default_value,
-- readonly}]). This works for us because the onboarding documents are
-- fixed legal boilerplate + ~19 discrete scalar blanks (see template_vars.py)
-- — every {{ variable }} maps 1:1 to a DocuSeal text field, and the lawyer's
-- clause wording lives in the template body.
--
-- This table stores the per-(org, envelope) DocuSeal template mapping HR
-- configures once. Two envelopes exist today, matching the two call sites in
-- routers/onboarding_v2.py:
--
--     template_key = 'loi'          → routed HR → candidate (2 roles)
--     template_key = 'offer_bundle' → candidate-only AL + NDA (1 role)
--
-- The DOCX templates on `documents` (template_kind) are still used for the
-- HR-facing PDF *preview* and for the print/scan fallback when DocuSeal isn't
-- configured. This table is purely the e-sign binding.
--
-- Idempotent — every statement re-runnable.

CREATE TABLE IF NOT EXISTS onboarding_docuseal_templates (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- Which onboarding signing envelope this template drives.
  --   'loi'          — LOI, routed HR (role 1) → candidate (role 2)
  --   'offer_bundle' — Appointment Letter + NDA, candidate-only (role 1)
  template_key          TEXT NOT NULL,

  -- The DocuSeal template id (integer in DocuSeal; stored as text so we never
  -- lose precision or fight the client's JSON number handling). Built once in
  -- the DocuSeal UI by uploading the org's document and placing fields.
  docuseal_template_id  TEXT NOT NULL,

  -- Ordered DocuSeal role names, as named in the template's field builder.
  -- Position maps to our internal routing order: role_names[0] is the first
  -- signer (HR for LOI; the candidate for offer_bundle), role_names[1] the
  -- second (candidate for LOI). DocuSeal's default role names are
  -- "First Party" / "Second Party" — HR can rename them and we mirror it here.
  role_names            TEXT[] NOT NULL DEFAULT ARRAY['First Party', 'Second Party'],

  -- Optional override map: our template_vars.py variable name → the field
  -- name as it appears in the DocuSeal template. Empty means identity
  -- (the DocuSeal field is named exactly like our variable, e.g.
  -- "candidate_name"). Only list variables whose DocuSeal field name differs.
  field_map             JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Free-text note for HR (e.g. "rebuilt 2026-07 after clause change").
  note                  TEXT,

  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (org_id, template_key)
);

ALTER TABLE onboarding_docuseal_templates
  DROP CONSTRAINT IF EXISTS onboarding_docuseal_templates_key_check;
ALTER TABLE onboarding_docuseal_templates
  ADD CONSTRAINT onboarding_docuseal_templates_key_check CHECK (
    template_key IN ('loi', 'offer_bundle')
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_docuseal_templates_org
  ON onboarding_docuseal_templates (org_id, template_key);

ALTER TABLE onboarding_docuseal_templates ENABLE ROW LEVEL SECURITY;

-- Read scoped to the caller's org (mirrors onboarding_runs / onboarding_documents).
DROP POLICY IF EXISTS onboarding_docuseal_templates_select ON onboarding_docuseal_templates;
CREATE POLICY onboarding_docuseal_templates_select ON onboarding_docuseal_templates
  FOR SELECT USING (org_id = auth_org_id());

-- Mutations go through the service-role client in the settings router
-- (mirrors the rest of the onboarding tables), so no INSERT/UPDATE/DELETE
-- policy is defined.

DROP TRIGGER IF EXISTS trg_onboarding_docuseal_templates_updated_at ON onboarding_docuseal_templates;
CREATE TRIGGER trg_onboarding_docuseal_templates_updated_at
  BEFORE UPDATE ON onboarding_docuseal_templates
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE onboarding_docuseal_templates IS
  'Per-(org, envelope) DocuSeal template binding for onboarding e-sign. Free '
  'DocuSeal cannot ingest a PDF via API, so we POST /submissions against a '
  'template_id built once in the DocuSeal UI and pre-fill per-candidate data '
  'as read-only field values. template_key: LOI(routed) | offer_bundle (AL+NDA).';
