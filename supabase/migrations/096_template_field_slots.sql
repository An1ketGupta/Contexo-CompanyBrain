-- ── 096: Template field slots — direct-fill pipeline (replaces Jinja baking) ──
--
-- Why this migration exists
-- -------------------------
-- The onboarding template pipeline used to detect blanks in an HR-uploaded
-- .docx, then REWRITE that .docx to contain `{{ variable }}` Jinja tags, and
-- render it with docxtpl at generation time. Two failure modes made that
-- untenable for arbitrary customer documents:
--
--   1. Any human-typed bracket text that merely LOOKED like a tag (e.g.
--      `{{ Signing Date }}` — two words, not a valid Jinja expression) was
--      trusted as "already templated" and handed straight to docxtpl, which
--      failed at render time with an opaque parser error
--      ("expected token 'end of print statement', got 'signing'").
--
--   2. A genuinely EMPTY fill-spot — a label like "Employee Signature:" with
--      nothing after it, or an empty table cell beside a labelled one — had no
--      marker characters to regex against, so it was invisible: no candidate,
--      no error, and a document that shipped with the field silently blank.
--
-- The new pipeline never writes template syntax into a customer's document.
-- Detected fill-points are persisted here as *slots* (an anchor position + an
-- action + the variable that fills it); at generation time the renderer splices
-- real values straight into the DOCX runs at those positions. A customer
-- document therefore never has to be valid template-language source — it only
-- has to have locatable fill-points.
--
-- Coexistence, not cutover
-- ------------------------
-- Two flags gate this, because they answer two different questions:
--
--   * `documents.fill_strategy` (NULL | 'jinja' | 'slots') — per-template,
--     decided once at analyze time. NULL means "never analyzed under the
--     slots-aware analyzer" and the agent keeps using the legacy docxtpl
--     renderer. Every template that is active today is NULL and stays NULL
--     until HR explicitly reopens its mapper. No forced re-analysis, ever.
--
--   * `organizations.onboarding_template_pipeline` ('jinja' | 'slots') — the
--     rollout gate, default 'jinja'. Controls what a FRESH analyze of an
--     untemplated document is allowed to produce. Flipping one org to 'slots'
--     is the pilot switch and changes nothing for any other org.
--
-- Idempotent — every statement re-runnable.

-- ── 1. organizations.onboarding_template_pipeline — the rollout gate ───────

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS onboarding_template_pipeline TEXT NOT NULL DEFAULT 'jinja';

ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_template_pipeline_check;
ALTER TABLE organizations
  ADD CONSTRAINT organizations_template_pipeline_check CHECK (
    onboarding_template_pipeline IN ('jinja', 'slots')
  );

COMMENT ON COLUMN organizations.onboarding_template_pipeline IS
  'Which template-fill pipeline a NEW analyze may choose for this org. '
  'jinja (default) = legacy behaviour, blanks are baked into the .docx as '
  '{{ tags }} and rendered with docxtpl. slots = blanks are persisted as '
  'template_field_slots rows and rendered by direct substitution. Flip a '
  'single org to slots to pilot; already-active templates are unaffected '
  'either way (see documents.fill_strategy).';


-- ── 2. documents.fill_strategy — per-template pipeline decision ────────────
-- Deliberately nullable with no default: NULL is the "legacy, untouched"
-- state that every pre-096 template row already sits in, and the agent reads
-- it as "use docxtpl". A row only leaves NULL when an analyze under the new
-- code path explicitly classifies it.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS fill_strategy TEXT;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_fill_strategy_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_fill_strategy_check CHECK (
    fill_strategy IS NULL OR fill_strategy IN ('jinja', 'slots')
  );

COMMENT ON COLUMN documents.fill_strategy IS
  'How this template gets filled. NULL = never analyzed under the '
  'slots-aware analyzer; the agent renders it with the legacy docxtpl path. '
  'jinja = the document contains hand-authored, syntactically VALID {{ }} '
  'tags (power-template opt-in), rendered with docxtpl. slots = fill-points '
  'live in template_field_slots and are spliced in directly, with no '
  'templating engine involved. Decided once per document at analyze time and '
  'never auto-migrated.';


-- ── 3. template_field_slots ────────────────────────────────────────────────
-- One row per detected (or manually added) fill-point in a template.
--
-- Anchoring: `paragraph_index` is the position in the canonical paragraph
-- enumeration produced by
-- apps/api/app/services/agents/onboarding_v2/docx_positions.canonical_paragraphs()
-- — body paragraphs and table cells in true document order, then headers and
-- footers. Detection and rendering MUST call that same function, which is why
-- it lives in a shared module rather than inside the analyzer.
--
-- Drift: `paragraph_hash` is sha256 of that one paragraph's run text at
-- detection time. The render-time guard re-hashes ONLY the paragraphs it is
-- about to write into, so HR editing unrelated wording elsewhere in a long
-- document never invalidates a confirmed slot. `source_docx_sha256` is the
-- coarse whole-file equivalent, used to tell the UI "this template changed
-- since these slots were detected, re-analyze" in one comparison.
--
-- Note on document_version_id: it is nullable and INFORMATIONAL only. It is
-- not the drift key, because the template-editing endpoints overwrite the
-- Storage object in place without minting a new document_versions row (and
-- legacy single-version templates have no version row at all). The hashes
-- above are the real integrity check.

CREATE TABLE IF NOT EXISTS template_field_slots (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  document_version_id  UUID REFERENCES document_versions(id) ON DELETE SET NULL,

  -- Anchor
  paragraph_index      INTEGER NOT NULL,
  paragraph_kind       TEXT NOT NULL DEFAULT 'body',
  paragraph_hash       TEXT NOT NULL,
  source_docx_sha256   TEXT NOT NULL,

  -- Span. For the insert_* actions start_offset = end_offset (an insertion
  -- point, not a range) and blank_text is ''.
  start_offset         INTEGER NOT NULL,
  end_offset           INTEGER NOT NULL,
  action               TEXT NOT NULL DEFAULT 'replace_span',
  blank_text           TEXT NOT NULL DEFAULT '',
  context_before       TEXT NOT NULL DEFAULT '',
  context_after        TEXT NOT NULL DEFAULT '',

  -- Classification. variable stays NULL for a slot the classifier could not
  -- map (HR picks one, or rejects the slot).
  variable             TEXT,
  confidence           TEXT NOT NULL DEFAULT 'medium',

  -- Review workflow. Only `confirmed` slots are ever rendered.
  status               TEXT NOT NULL DEFAULT 'proposed',
  source               TEXT NOT NULL DEFAULT 'ai',

  confirmed_by         UUID REFERENCES users(id) ON DELETE SET NULL,
  confirmed_at         TIMESTAMPTZ,

  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_action_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_action_check CHECK (
    action IN ('replace_span', 'insert_after_label', 'insert_empty_cell')
  );

ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_kind_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_kind_check CHECK (
    paragraph_kind IN ('body', 'table', 'header', 'footer')
  );

ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_confidence_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_confidence_check CHECK (
    confidence IN ('high', 'medium', 'low')
  );

ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_status_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_status_check CHECK (
    status IN ('proposed', 'confirmed', 'rejected')
  );

ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_source_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_source_check CHECK (
    source IN ('ai', 'manual')
  );

-- A confirmed slot must know what fills it. Enforced in the DB and not just
-- the router because a confirmed slot with a NULL variable would render as an
-- empty string in a legally-binding document.
ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_confirmed_var_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_confirmed_var_check CHECK (
    status <> 'confirmed' OR variable IS NOT NULL
  );

ALTER TABLE template_field_slots DROP CONSTRAINT IF EXISTS template_field_slots_offsets_check;
ALTER TABLE template_field_slots
  ADD CONSTRAINT template_field_slots_offsets_check CHECK (
    start_offset >= 0 AND end_offset >= start_offset
  );

-- Natural key: one slot per anchor per template. Re-running analyze UPSERTS
-- proposals at the same anchor rather than duplicating rows, and the upsert
-- helper refuses to touch a row HR has already confirmed or rejected — so
-- re-analyzing is always safe and never silently undoes a human decision.
-- `action` is deliberately NOT part of the key: two slots writing to the same
-- anchor via different actions would double-write that spot.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_template_field_slots_anchor
  ON template_field_slots (document_id, paragraph_index, start_offset, end_offset);

CREATE INDEX IF NOT EXISTS idx_template_field_slots_document
  ON template_field_slots (document_id, status);

CREATE INDEX IF NOT EXISTS idx_template_field_slots_org
  ON template_field_slots (org_id, document_id);

ALTER TABLE template_field_slots ENABLE ROW LEVEL SECURITY;

-- Select-only for end users; every mutation goes through the service-role
-- client in apps/api. Same posture as onboarding_runs / onboarding_documents.
DROP POLICY IF EXISTS template_field_slots_select ON template_field_slots;
CREATE POLICY template_field_slots_select ON template_field_slots
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_template_field_slots_updated_at ON template_field_slots;
CREATE TRIGGER trg_template_field_slots_updated_at
  BEFORE UPDATE ON template_field_slots
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE template_field_slots IS
  'Persisted fill-points for a slots-strategy onboarding template. Each row '
  'is an anchor (paragraph_index + offsets in canonical_paragraphs order), an '
  'action, and the template variable that fills it. Replaces the old approach '
  'of rewriting the customer .docx to contain {{ Jinja }} tags — nothing is '
  'ever written into the template document itself.';

COMMENT ON COLUMN template_field_slots.paragraph_hash IS
  'sha256 of the anchor paragraph''s run text at detection time. Re-checked '
  'per-paragraph immediately before rendering; a mismatch means HR edited '
  'THAT paragraph since confirming, and the run is parked in '
  'blocked_template_drift rather than written to the wrong offset.';

COMMENT ON COLUMN template_field_slots.action IS
  'replace_span = overwrite the text between start_offset and end_offset (an '
  '________ / [bracket] / <angle> blank). insert_after_label = append the '
  'value at the end of a label-with-nothing-after-it paragraph ("Employee '
  'Signature:"). insert_empty_cell = write the value into an empty table cell '
  'sitting beside a labelled one. The two insert_* actions are the cases the '
  'old regex-only detector could not see at all.';

COMMENT ON COLUMN template_field_slots.status IS
  'proposed = the classifier suggested it, awaiting HR review (never '
  'rendered). confirmed = HR approved it; only these are rendered. rejected = '
  'HR declined it; kept as a row so re-analyzing does not keep re-proposing '
  'the same wrong mapping.';

COMMENT ON COLUMN template_field_slots.source IS
  'ai = proposed by the classifier. manual = HR added this fill-point by hand '
  'because detection missed it — the recall safety net for documents whose '
  'blanks have no detectable shape at all.';


-- ── 4. onboarding_runs.status — new blocked state for slot drift ───────────
-- Distinct from blocked_missing_template (no active template at all): here a
-- template EXISTS and is active, but a confirmed slot's anchor paragraph has
-- changed since HR confirmed it, so we refuse to write a value to an offset
-- we can no longer vouch for.

ALTER TABLE onboarding_runs DROP CONSTRAINT IF EXISTS onboarding_runs_status_check;
ALTER TABLE onboarding_runs
  ADD CONSTRAINT onboarding_runs_status_check CHECK (
    status IN (
      'draft',
      'loi_generating', 'loi_pending_hr_review', 'loi_pending_hr_sign',
      'loi_pending_esign_signature',
      'loi_signed_uploaded', 'loi_sent_to_candidate',
      'awaiting_candidate_references',
      'bgv_pending', 'bgv_complete',
      'appointment_bundle_generating', 'appointment_pending_hr_review',
      'appointment_sent_to_candidate',
      'policies_assigned', 'policies_acknowledged',
      'induction_generating', 'induction_sent',
      'completed',
      'blocked_missing_template', 'blocked_template_drift',
      'failed', 'cancelled'
    )
  );
