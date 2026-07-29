-- ── 099: Document generation pipeline — templates, schema, generation ──────
--
-- Replaces the onboarding template pipeline built across 071 / 074 / 096 / 097.
--
-- What was wrong with the old shape
-- ---------------------------------
-- A template was not an entity. It was a `documents` row wearing two extra
-- columns (`template_kind`, `template_status`) and its fill-points lived in
-- `template_field_slots`, which stored the variable as a *text column on the
-- anchor row*. Three consequences, all of them load-bearing here:
--
--   1. One variable appearing in six places was six unrelated rows. There was
--      nowhere to hang a data type, a validation rule, a default, or a
--      description, because there was no row that meant "the variable" as
--      distinct from "a place it appears".
--
--   2. `documents.template_kind` was a CHECK constraint, so every new document
--      type a customer wanted was a migration. 076 widened it, 092 narrowed it
--      back. That churn is the symptom, not the cause.
--
--   3. Versions existed for KB documents but not for templates: the editing
--      endpoints overwrote the Storage object in place, which is why 096 had to
--      note that `document_version_id` was "INFORMATIONAL only" and fall back
--      to hashes as the real integrity check.
--
-- The shape here
-- --------------
--   document_types           what kinds of document exist (data, not a CHECK)
--   doc_templates            the library entry — one per (org, named template)
--   doc_template_versions    immutable uploaded files; the original is never
--                            modified, only superseded
--   doc_template_variables   the *definition* of a field: type, validation,
--                            default, description, confidence, HR decision
--   doc_template_slots       the *positions* a variable is written to; N per
--                            variable
--   doc_variable_mappings    where a variable's value comes from, configurable
--                            per org / document type / version
--   generated_documents      one row per generation attempt, never overwritten
--   generated_files          the DOCX and PDF artifacts of a generation
--   document_audit_logs      every action, by anyone, throughout the workflow
--
-- Templates remain immutable: nothing in this schema stores a rewritten copy of
-- a customer's file. Slots are anchors into the original bytes, and the
-- renderer splices values in at those anchors at generation time.
--
-- Idempotent — every statement re-runnable.


-- ── 1. document_types ──────────────────────────────────────────────────────
-- Data, not a CHECK constraint. `org_id IS NULL` marks a system-provided type
-- visible to every org; a non-NULL org_id is a type that one customer added for
-- themselves. Adding "Consultant Agreement" is an INSERT, which is the whole
-- point — new document types must not require a code change.

CREATE TABLE IF NOT EXISTS document_types (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID REFERENCES organizations(id) ON DELETE CASCADE,
  key          TEXT NOT NULL,
  label        TEXT NOT NULL,
  description  TEXT,
  is_system    BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order   INTEGER NOT NULL DEFAULT 100,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keys are slugs the API and agent refer to types by, so they must be stable
-- and collision-free within the scope an org can see: their own types plus the
-- system ones. Two partial uniques rather than one composite, because NULL
-- org_id would defeat a plain UNIQUE (org_id, key).
CREATE UNIQUE INDEX IF NOT EXISTS uniq_document_types_org_key
  ON document_types (org_id, key) WHERE org_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_document_types_system_key
  ON document_types (key) WHERE org_id IS NULL;

ALTER TABLE document_types ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_types_select ON document_types;
CREATE POLICY document_types_select ON document_types
  FOR SELECT USING (org_id IS NULL OR org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_document_types_updated_at ON document_types;
CREATE TRIGGER trg_document_types_updated_at
  BEFORE UPDATE ON document_types
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE document_types IS
  'The kinds of document a template can be. Deliberately a table and not a '
  'CHECK constraint: documents.template_kind was a CHECK, and every customer '
  'asking for a new type meant a migration (076 widened it, 092 narrowed it). '
  'org_id NULL = system type available to all orgs.';

-- Seed the system types. ON CONFLICT DO NOTHING keeps this re-runnable, and
-- the labels are what HR sees in the type picker.
INSERT INTO document_types (org_id, key, label, description, is_system, sort_order) VALUES
  (NULL, 'offer_letter',           'Offer Letter',             'Initial offer of employment extended to a candidate.',        TRUE, 10),
  (NULL, 'letter_of_intent',       'Letter of Intent',         'Non-binding statement of intent to hire, issued pre-offer.',  TRUE, 20),
  (NULL, 'appointment_letter',     'Appointment Letter',       'Formal appointment issued after the offer is accepted.',      TRUE, 30),
  (NULL, 'employment_agreement',   'Employment Agreement',     'Full employment contract setting out terms and conditions.',  TRUE, 40),
  (NULL, 'nda',                    'NDA',                      'Non-disclosure agreement.',                                   TRUE, 50),
  (NULL, 'confidentiality_agreement','Confidentiality Agreement','Standalone confidentiality undertaking.',                    TRUE, 60),
  (NULL, 'internship_agreement',   'Internship Agreement',     'Agreement governing an internship engagement.',               TRUE, 70),
  (NULL, 'consultant_agreement',   'Consultant Agreement',     'Agreement for an engaged consultant.',                        TRUE, 80),
  (NULL, 'contractor_agreement',   'Contractor Agreement',     'Agreement for an independent contractor.',                    TRUE, 90),
  (NULL, 'induction',              'Induction Pack',           'Joining / induction document sent ahead of the start date.',  TRUE, 100)
ON CONFLICT DO NOTHING;


-- ── 2. doc_templates ───────────────────────────────────────────────────────
-- The library entry. Carries identity and lifecycle; the bytes live on
-- versions. `current_version_id` is added as a constraint further down, after
-- doc_template_versions exists (the two reference each other).

CREATE TABLE IF NOT EXISTS doc_templates (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_type_id    UUID NOT NULL REFERENCES document_types(id) ON DELETE RESTRICT,

  name                TEXT NOT NULL,
  description         TEXT,

  status              TEXT NOT NULL DEFAULT 'draft',
  is_default          BOOLEAN NOT NULL DEFAULT FALSE,

  current_version_id  UUID,

  created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE doc_templates DROP CONSTRAINT IF EXISTS doc_templates_status_check;
ALTER TABLE doc_templates
  ADD CONSTRAINT doc_templates_status_check CHECK (
    status IN ('draft', 'active', 'archived')
  );

-- One default per (org, type). The agent asks for "the org's LOI template" and
-- must get a deterministic answer; the old code did
-- `order(created_at desc).limit(1)`, which silently changed which template was
-- live whenever someone uploaded another one.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_templates_default
  ON doc_templates (org_id, document_type_id)
  WHERE is_default AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_doc_templates_org
  ON doc_templates (org_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_doc_templates_type
  ON doc_templates (org_id, document_type_id, status);

ALTER TABLE doc_templates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_templates_select ON doc_templates;
CREATE POLICY doc_templates_select ON doc_templates
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_doc_templates_updated_at ON doc_templates;
CREATE TRIGGER trg_doc_templates_updated_at
  BEFORE UPDATE ON doc_templates
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE doc_templates IS
  'A named, reusable document template in an org''s library. Identity and '
  'lifecycle only — the uploaded bytes live on doc_template_versions, and are '
  'never modified in place.';

COMMENT ON COLUMN doc_templates.is_default IS
  'The template an automated generation picks when asked only for a document '
  'type. Partial-unique with status=active so exactly one can hold the role.';


-- ── 3. doc_template_versions ───────────────────────────────────────────────
-- Immutable. A re-upload mints a new row; the previous file stays downloadable
-- and any generated_documents that referenced it keep pointing at the exact
-- bytes they were produced from.

CREATE TABLE IF NOT EXISTS doc_template_versions (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  template_id        UUID NOT NULL REFERENCES doc_templates(id) ON DELETE CASCADE,

  version_no         INTEGER NOT NULL,

  storage_path       TEXT NOT NULL,
  original_filename  TEXT NOT NULL,
  mime_type          TEXT NOT NULL,
  file_bytes         BIGINT,
  file_sha256        TEXT NOT NULL,

  -- What the analyzer concluded, kept separate from the template's declared
  -- type so a disagreement is visible to HR rather than silently overriding
  -- their choice.
  detected_type_id   UUID REFERENCES document_types(id) ON DELETE SET NULL,
  detected_type_confidence NUMERIC(5, 4),

  analysis_status    TEXT NOT NULL DEFAULT 'pending',
  analysis_error     TEXT,
  analyzed_at        TIMESTAMPTZ,
  schema_version     INTEGER NOT NULL DEFAULT 1,

  uploaded_by        UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE doc_template_versions DROP CONSTRAINT IF EXISTS doc_template_versions_analysis_check;
ALTER TABLE doc_template_versions
  ADD CONSTRAINT doc_template_versions_analysis_check CHECK (
    analysis_status IN ('pending', 'analyzing', 'completed', 'failed', 'manual')
  );

ALTER TABLE doc_template_versions DROP CONSTRAINT IF EXISTS doc_template_versions_mime_check;
ALTER TABLE doc_template_versions
  ADD CONSTRAINT doc_template_versions_mime_check CHECK (
    mime_type IN (
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/pdf'
    )
  );

CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_template_versions_no
  ON doc_template_versions (template_id, version_no);

CREATE INDEX IF NOT EXISTS idx_doc_template_versions_template
  ON doc_template_versions (template_id, version_no DESC);

CREATE INDEX IF NOT EXISTS idx_doc_template_versions_org
  ON doc_template_versions (org_id, created_at DESC);

ALTER TABLE doc_template_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_template_versions_select ON doc_template_versions;
CREATE POLICY doc_template_versions_select ON doc_template_versions
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_doc_template_versions_updated_at ON doc_template_versions;
CREATE TRIGGER trg_doc_template_versions_updated_at
  BEFORE UPDATE ON doc_template_versions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE doc_template_versions IS
  'An uploaded template file. Immutable: the stored object is never rewritten, '
  'so the original a customer uploaded is always recoverable byte-for-byte. '
  'Editing a template means uploading a new version.';

COMMENT ON COLUMN doc_template_versions.analysis_status IS
  'pending = queued. analyzing = in flight. completed = variables and slots '
  'were produced. failed = analysis errored; analysis_error says why and HR '
  'can still define variables by hand. manual = HR skipped AI analysis '
  'entirely and is defining fields themselves.';

COMMENT ON COLUMN doc_template_versions.file_sha256 IS
  'sha256 of the stored file. Slots record the same hash so the renderer can '
  'refuse to write into bytes that are not the ones the fields were detected '
  'against.';

-- Now that versions exist, close the loop from doc_templates.
ALTER TABLE doc_templates DROP CONSTRAINT IF EXISTS doc_templates_current_version_fk;
ALTER TABLE doc_templates
  ADD CONSTRAINT doc_templates_current_version_fk
  FOREIGN KEY (current_version_id) REFERENCES doc_template_versions(id) ON DELETE SET NULL;

COMMENT ON COLUMN doc_templates.current_version_id IS
  'The version generation reads. Points at a doc_template_versions row; '
  'promoting a new version is a single UPDATE here, and never touches the '
  'bytes of any version.';


-- ── 4. doc_template_variables ──────────────────────────────────────────────
-- The *definition* of a field. This is the row that did not exist before, and
-- its absence is why the old pipeline could not carry a data type, a validation
-- rule, a default, or a description.
--
-- Scoped to a version, not a template: a new upload can legitimately introduce,
-- rename, or drop fields, and the variables of the version a document was
-- generated from must stay exactly as they were.

CREATE TABLE IF NOT EXISTS doc_template_variables (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  version_id        UUID NOT NULL REFERENCES doc_template_versions(id) ON DELETE CASCADE,

  internal_name     TEXT NOT NULL,
  display_name      TEXT NOT NULL,
  description       TEXT,

  data_type         TEXT NOT NULL DEFAULT 'text',
  is_required       BOOLEAN NOT NULL DEFAULT TRUE,
  default_value     TEXT,
  validation_rules  JSONB NOT NULL DEFAULT '{}',
  aliases           TEXT[] NOT NULL DEFAULT '{}',
  example_value     TEXT,

  -- Numeric so a threshold is a comparison, not a bucket lookup. The old
  -- schema stored high/medium/low, which cannot express "the threshold is 0.85".
  confidence        NUMERIC(5, 4),

  status            TEXT NOT NULL DEFAULT 'proposed',
  source            TEXT NOT NULL DEFAULT 'ai',

  confirmed_by      UUID REFERENCES users(id) ON DELETE SET NULL,
  confirmed_at      TIMESTAMPTZ,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The type vocabulary from the spec, plus signature_block. Signatures are out
-- of scope for the generation workflow itself, but apps/esign still locates
-- signer fields by finding sentinel strings in the rendered PDF, so the
-- variable that emits those sentinels needs a type that the validation engine
-- knows to skip.
ALTER TABLE doc_template_variables DROP CONSTRAINT IF EXISTS doc_template_variables_type_check;
ALTER TABLE doc_template_variables
  ADD CONSTRAINT doc_template_variables_type_check CHECK (
    data_type IN (
      'text', 'email', 'phone', 'currency', 'date', 'number', 'boolean',
      'address', 'country', 'state', 'city', 'designation', 'department',
      'manager', 'company', 'signature_block', 'custom'
    )
  );

ALTER TABLE doc_template_variables DROP CONSTRAINT IF EXISTS doc_template_variables_status_check;
ALTER TABLE doc_template_variables
  ADD CONSTRAINT doc_template_variables_status_check CHECK (
    status IN ('proposed', 'confirmed', 'rejected')
  );

ALTER TABLE doc_template_variables DROP CONSTRAINT IF EXISTS doc_template_variables_source_check;
ALTER TABLE doc_template_variables
  ADD CONSTRAINT doc_template_variables_source_check CHECK (
    source IN ('ai', 'manual')
  );

ALTER TABLE doc_template_variables DROP CONSTRAINT IF EXISTS doc_template_variables_confidence_check;
ALTER TABLE doc_template_variables
  ADD CONSTRAINT doc_template_variables_confidence_check CHECK (
    confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
  );

-- internal_name is what mappings and the renderer key on, so it must be a
-- stable identifier rather than free text.
ALTER TABLE doc_template_variables DROP CONSTRAINT IF EXISTS doc_template_variables_name_check;
ALTER TABLE doc_template_variables
  ADD CONSTRAINT doc_template_variables_name_check CHECK (
    internal_name ~ '^[a-z][a-z0-9_]*$'
  );

CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_template_variables_name
  ON doc_template_variables (version_id, internal_name);

CREATE INDEX IF NOT EXISTS idx_doc_template_variables_version
  ON doc_template_variables (version_id, status);

ALTER TABLE doc_template_variables ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_template_variables_select ON doc_template_variables;
CREATE POLICY doc_template_variables_select ON doc_template_variables
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_doc_template_variables_updated_at ON doc_template_variables;
CREATE TRIGGER trg_doc_template_variables_updated_at
  BEFORE UPDATE ON doc_template_variables
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE doc_template_variables IS
  'The definition of one field in a template version: its type, whether it is '
  'required, its default, its validation rules, and HR''s decision on it. One '
  'row per variable regardless of how many places in the document it fills — '
  'those are doc_template_slots rows pointing here.';

COMMENT ON COLUMN doc_template_variables.validation_rules IS
  'Type-specific constraints applied by the validation engine before '
  'generation. Recognised keys depend on data_type — e.g. {"min_length": 2} '
  'for text, {"not_past": true} for date, {"min": 0} for currency/number, '
  '{"pattern": "..."} for any. Unknown keys are ignored, so adding a rule is '
  'a code change in the engine and never a migration here.';

COMMENT ON COLUMN doc_template_variables.aliases IS
  'Alternative names this field is known by in source systems, used by the '
  'mapping engine to auto-suggest a source before a human configures one.';

COMMENT ON COLUMN doc_template_variables.confidence IS
  'Detection confidence in [0,1]. Numeric rather than high/medium/low so an '
  'org-configurable auto-confirm threshold is a comparison.';


-- ── 5. doc_template_slots ──────────────────────────────────────────────────
-- Where a variable gets written. Anchors into the ORIGINAL file: a paragraph
-- position in the canonical enumeration, plus a character span within it.
--
-- Nothing here is written into the customer's document — that is the whole
-- reason the anchor exists. The renderer opens the untouched original and
-- splices values in at these coordinates.

CREATE TABLE IF NOT EXISTS doc_template_slots (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  version_id          UUID NOT NULL REFERENCES doc_template_versions(id) ON DELETE CASCADE,
  variable_id         UUID REFERENCES doc_template_variables(id) ON DELETE SET NULL,

  -- Anchor. paragraph_index indexes the enumeration produced by
  -- app/services/documents/docx_positions.canonical_paragraphs(); detection and
  -- rendering MUST call that same function.
  paragraph_index     INTEGER NOT NULL,
  paragraph_kind      TEXT NOT NULL DEFAULT 'body',
  paragraph_hash      TEXT NOT NULL,
  source_file_sha256  TEXT NOT NULL,

  -- Span within the paragraph's run text. For insert_* actions
  -- start_offset = end_offset (an insertion point, not a range).
  start_offset        INTEGER NOT NULL,
  end_offset          INTEGER NOT NULL,
  action              TEXT NOT NULL DEFAULT 'replace_span',

  -- What was there when detected. For a literal-value detection this is the
  -- sample value ("Rahul"); for a blank it is the underscores or brackets.
  -- Kept so the builder UI can show HR exactly what will be overwritten.
  original_text       TEXT NOT NULL DEFAULT '',
  context_before      TEXT NOT NULL DEFAULT '',
  context_after       TEXT NOT NULL DEFAULT '',

  confidence          NUMERIC(5, 4),
  status              TEXT NOT NULL DEFAULT 'proposed',
  source              TEXT NOT NULL DEFAULT 'ai',

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE doc_template_slots DROP CONSTRAINT IF EXISTS doc_template_slots_action_check;
ALTER TABLE doc_template_slots
  ADD CONSTRAINT doc_template_slots_action_check CHECK (
    action IN ('replace_span', 'insert_after_label', 'insert_empty_cell')
  );

ALTER TABLE doc_template_slots DROP CONSTRAINT IF EXISTS doc_template_slots_kind_check;
ALTER TABLE doc_template_slots
  ADD CONSTRAINT doc_template_slots_kind_check CHECK (
    paragraph_kind IN ('body', 'table', 'header', 'footer')
  );

ALTER TABLE doc_template_slots DROP CONSTRAINT IF EXISTS doc_template_slots_status_check;
ALTER TABLE doc_template_slots
  ADD CONSTRAINT doc_template_slots_status_check CHECK (
    status IN ('proposed', 'confirmed', 'rejected')
  );

ALTER TABLE doc_template_slots DROP CONSTRAINT IF EXISTS doc_template_slots_source_check;
ALTER TABLE doc_template_slots
  ADD CONSTRAINT doc_template_slots_source_check CHECK (
    source IN ('ai', 'manual')
  );

ALTER TABLE doc_template_slots DROP CONSTRAINT IF EXISTS doc_template_slots_offsets_check;
ALTER TABLE doc_template_slots
  ADD CONSTRAINT doc_template_slots_offsets_check CHECK (
    start_offset >= 0 AND end_offset >= start_offset
  );

-- A confirmed slot must know which variable fills it. Enforced here and not
-- only in the router, because a confirmed slot with no variable renders as an
-- empty string in a legally-binding document.
ALTER TABLE doc_template_slots DROP CONSTRAINT IF EXISTS doc_template_slots_confirmed_var_check;
ALTER TABLE doc_template_slots
  ADD CONSTRAINT doc_template_slots_confirmed_var_check CHECK (
    status <> 'confirmed' OR variable_id IS NOT NULL
  );

-- Natural key: one slot per anchor per version. Re-running analysis upserts at
-- the same anchor instead of duplicating, and the upsert helper refuses to
-- overwrite a row HR has already decided on. `action` is deliberately excluded
-- — two actions writing the same anchor would double-write that spot.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_template_slots_anchor
  ON doc_template_slots (version_id, paragraph_index, start_offset, end_offset);

CREATE INDEX IF NOT EXISTS idx_doc_template_slots_version
  ON doc_template_slots (version_id, status);

CREATE INDEX IF NOT EXISTS idx_doc_template_slots_variable
  ON doc_template_slots (variable_id);

ALTER TABLE doc_template_slots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_template_slots_select ON doc_template_slots;
CREATE POLICY doc_template_slots_select ON doc_template_slots
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_doc_template_slots_updated_at ON doc_template_slots;
CREATE TRIGGER trg_doc_template_slots_updated_at
  BEFORE UPDATE ON doc_template_slots
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE doc_template_slots IS
  'A position in a template version where a variable''s value is written. '
  'Anchors into the ORIGINAL file — nothing is ever written into the stored '
  'template. Many slots may point at one doc_template_variables row.';

COMMENT ON COLUMN doc_template_slots.paragraph_hash IS
  'sha256 of the anchor paragraph''s run text at detection time. Re-checked '
  'per-paragraph immediately before rendering, so HR rewording an unrelated '
  'clause elsewhere in a long document never invalidates a confirmed slot.';

COMMENT ON COLUMN doc_template_slots.action IS
  'replace_span = overwrite the text between start_offset and end_offset (a '
  'blank, a bracket, or a literal sample value). insert_after_label = append '
  'at the end of a label paragraph with nothing after it ("Employee '
  'Signature:"). insert_empty_cell = write into an empty table cell beside a '
  'labelled one.';

COMMENT ON COLUMN doc_template_slots.original_text IS
  'The text occupying this span when it was detected — underscores, a bracket '
  'placeholder, or a literal sample value like "Rahul". Shown to HR in the '
  'builder so they can see precisely what a generation will overwrite.';


-- ── 6. doc_variable_mappings ───────────────────────────────────────────────
-- Where a variable's value comes from. Configuration, resolved most-specific
-- first: a mapping on the version beats one on the document type, which beats
-- the org-wide default.

CREATE TABLE IF NOT EXISTS doc_variable_mappings (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  scope             TEXT NOT NULL,
  document_type_id  UUID REFERENCES document_types(id) ON DELETE CASCADE,
  version_id        UUID REFERENCES doc_template_versions(id) ON DELETE CASCADE,

  internal_name     TEXT NOT NULL,
  -- Dotted path into the canonical candidate profile, e.g. 'candidate.full_name',
  -- 'offer.ctc_amount', 'company.registered_address'. Resolved by the mapping
  -- engine; an unresolvable path is a validation error, never a silent blank.
  source_path       TEXT NOT NULL,
  transform         TEXT,

  created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE doc_variable_mappings DROP CONSTRAINT IF EXISTS doc_variable_mappings_scope_check;
ALTER TABLE doc_variable_mappings
  ADD CONSTRAINT doc_variable_mappings_scope_check CHECK (
    (scope = 'org'             AND document_type_id IS NULL     AND version_id IS NULL) OR
    (scope = 'document_type'   AND document_type_id IS NOT NULL AND version_id IS NULL) OR
    (scope = 'template_version' AND document_type_id IS NULL    AND version_id IS NOT NULL)
  );

CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_variable_mappings_org
  ON doc_variable_mappings (org_id, internal_name) WHERE scope = 'org';

CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_variable_mappings_type
  ON doc_variable_mappings (org_id, document_type_id, internal_name) WHERE scope = 'document_type';

CREATE UNIQUE INDEX IF NOT EXISTS uniq_doc_variable_mappings_version
  ON doc_variable_mappings (version_id, internal_name) WHERE scope = 'template_version';

ALTER TABLE doc_variable_mappings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_variable_mappings_select ON doc_variable_mappings;
CREATE POLICY doc_variable_mappings_select ON doc_variable_mappings
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_doc_variable_mappings_updated_at ON doc_variable_mappings;
CREATE TRIGGER trg_doc_variable_mappings_updated_at
  BEFORE UPDATE ON doc_variable_mappings
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE doc_variable_mappings IS
  'Configurable variable -> data-source bindings. Resolution is most-specific '
  'first: template_version, then document_type, then org. A variable with no '
  'mapping at any scope falls back to the engine''s built-in default path for '
  'that internal_name, and failing that becomes a validation error.';

COMMENT ON COLUMN doc_variable_mappings.transform IS
  'Optional named transform applied to the resolved value (e.g. upper, '
  'title_case, date_long, currency_inr). Named rather than expression-based '
  'on purpose: an eval-able expression in a legally-binding document pipeline '
  'is an injection surface.';


-- ── 7. generated_documents ─────────────────────────────────────────────────
-- One row per generation attempt. Never overwritten — regenerating mints a new
-- row with the next generation_no, so the artifact a candidate was actually
-- sent is always recoverable.

CREATE TABLE IF NOT EXISTS generated_documents (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  template_id            UUID NOT NULL REFERENCES doc_templates(id) ON DELETE RESTRICT,
  version_id             UUID NOT NULL REFERENCES doc_template_versions(id) ON DELETE RESTRICT,

  -- Subject of the document. All nullable: a document may be generated for an
  -- onboarding run, straight from an ATS candidate, or from HR-entered data
  -- with no upstream record at all.
  onboarding_run_id      UUID REFERENCES onboarding_runs(id) ON DELETE SET NULL,
  requisition_id         UUID REFERENCES job_requisitions(id) ON DELETE SET NULL,
  candidate_id           UUID REFERENCES recruiting_candidates(id) ON DELETE SET NULL,

  generation_no          INTEGER NOT NULL DEFAULT 1,

  -- Immutable evidence of what produced this document.
  candidate_snapshot     JSONB NOT NULL DEFAULT '{}',
  context_snapshot       JSONB NOT NULL DEFAULT '{}',
  validation_report      JSONB NOT NULL DEFAULT '{}',

  status                 TEXT NOT NULL DEFAULT 'pending',
  error_message          TEXT,

  generated_at           TIMESTAMPTZ,
  approved_by            UUID REFERENCES users(id) ON DELETE SET NULL,
  approved_at            TIMESTAMPTZ,
  rejected_by            UUID REFERENCES users(id) ON DELETE SET NULL,
  rejected_at            TIMESTAMPTZ,
  rejection_reason       TEXT,
  sent_at                TIMESTAMPTZ,
  sent_to                TEXT,

  created_by             UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE generated_documents DROP CONSTRAINT IF EXISTS generated_documents_status_check;
ALTER TABLE generated_documents
  ADD CONSTRAINT generated_documents_status_check CHECK (
    status IN (
      'pending',
      'validation_failed',
      'generating',
      'generation_failed',
      'generated',
      'approved',
      'rejected',
      'sending',
      'sent',
      'send_failed'
    )
  );

CREATE INDEX IF NOT EXISTS idx_generated_documents_org
  ON generated_documents (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_generated_documents_run
  ON generated_documents (onboarding_run_id, created_at DESC)
  WHERE onboarding_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_generated_documents_version
  ON generated_documents (version_id, created_at DESC);

ALTER TABLE generated_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS generated_documents_select ON generated_documents;
CREATE POLICY generated_documents_select ON generated_documents
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_generated_documents_updated_at ON generated_documents;
CREATE TRIGGER trg_generated_documents_updated_at
  BEFORE UPDATE ON generated_documents
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE generated_documents IS
  'One generation attempt. Regenerating never overwrites: a new row is minted '
  'with the next generation_no, so the exact document a candidate received '
  'stays reconstructable along with the data and template version behind it.';

COMMENT ON COLUMN generated_documents.candidate_snapshot IS
  'The canonical candidate profile as resolved at generation time. Frozen '
  'because the upstream ATS row can change afterwards and must not '
  'retroactively contradict a document already sent.';

COMMENT ON COLUMN generated_documents.context_snapshot IS
  'The exact variable -> value map spliced into the document. Together with '
  'version_id this is enough to reproduce the artifact byte-for-byte.';

COMMENT ON COLUMN generated_documents.validation_report IS
  'Structured output of the validation engine: {"errors": [...], '
  '"warnings": [...]}. Persisted even on success so an approval can be '
  'audited against what was known at the time.';


-- ── 8. generated_files ─────────────────────────────────────────────────────
-- The artifacts. DOCX is authoritative; PDF is derived and may legitimately be
-- absent if conversion failed — the spec requires the DOCX to remain available
-- in that case.

CREATE TABLE IF NOT EXISTS generated_files (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  generated_document_id  UUID NOT NULL REFERENCES generated_documents(id) ON DELETE CASCADE,

  format                 TEXT NOT NULL,
  storage_path           TEXT NOT NULL,
  file_bytes             BIGINT,
  file_sha256            TEXT,

  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE generated_files DROP CONSTRAINT IF EXISTS generated_files_format_check;
ALTER TABLE generated_files
  ADD CONSTRAINT generated_files_format_check CHECK (format IN ('docx', 'pdf'));

CREATE UNIQUE INDEX IF NOT EXISTS uniq_generated_files_format
  ON generated_files (generated_document_id, format);

CREATE INDEX IF NOT EXISTS idx_generated_files_org
  ON generated_files (org_id, created_at DESC);

ALTER TABLE generated_files ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS generated_files_select ON generated_files;
CREATE POLICY generated_files_select ON generated_files
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE generated_files IS
  'Artifacts of one generation. Storage paths include the generation_no, so a '
  'regeneration writes new objects rather than replacing the bytes a previous '
  'generation produced.';

COMMENT ON COLUMN generated_files.format IS
  'docx is authoritative and always written. pdf is derived via Gotenberg and '
  'may be missing when conversion failed — by design, since a failed PDF '
  'conversion must not cost HR the generated DOCX.';


-- ── 9. document_audit_logs ─────────────────────────────────────────────────
-- Every action across the workflow.
--
-- `action` deliberately has NO CHECK constraint. recruiting_audit_log took the
-- opposite approach and 068 and 070 both had to run DO-blocks to discover and
-- drop the constraint by name just to add one value. An audit log's whole job
-- is to accept whatever happened.

CREATE TABLE IF NOT EXISTS document_audit_logs (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  actor_kind     TEXT NOT NULL DEFAULT 'user',
  actor_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,

  action         TEXT NOT NULL,
  entity_type    TEXT NOT NULL,
  entity_id      UUID,

  payload        JSONB NOT NULL DEFAULT '{}',

  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE document_audit_logs DROP CONSTRAINT IF EXISTS document_audit_logs_actor_check;
ALTER TABLE document_audit_logs
  ADD CONSTRAINT document_audit_logs_actor_check CHECK (
    actor_kind IN ('user', 'agent', 'system')
  );

CREATE INDEX IF NOT EXISTS idx_document_audit_logs_org
  ON document_audit_logs (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_document_audit_logs_entity
  ON document_audit_logs (entity_type, entity_id, created_at DESC);

ALTER TABLE document_audit_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_audit_logs_select ON document_audit_logs;
CREATE POLICY document_audit_logs_select ON document_audit_logs
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE document_audit_logs IS
  'Append-only record of every action in the document pipeline — uploads, '
  'analyses, variable decisions, mapping edits, validations, generations, '
  'approvals, sends. action is intentionally unconstrained text.';
