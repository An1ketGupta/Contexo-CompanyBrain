-- ── 107: Onboarding step engine — configurable steps, bundles, collection ──
--
-- Why this migration exists
-- -------------------------
-- The onboarding pipeline shipped in 071 as a fixed state machine: LOI → BGV →
-- Appointment Letter + NDA → Policies → Induction, encoded as a CHECK
-- constraint on `onboarding_runs.status` and an if/elif ladder in
-- OnboardingV2Agent.run(). Per-org flexibility arrived later as four booleans
-- in `organizations.metadata.onboarding_v2_steps`, which can only *skip* one of
-- the five steps the platform already knows about.
--
-- That shape cannot express what companies actually do. The immediate gap is
-- document *collection* — asking a candidate to submit 10th/12th marksheets,
-- PAN, relieving letter, bank proof — which is not a variant of any existing
-- step, has no fixed item list, and belongs at a position each org chooses.
-- Three more limits show up right behind it: which parties must sign is
-- hardcoded per document kind, bundling exists only as the literal
-- appointment_letter+nda pair, and `onboarding_documents.kind` is a CHECK, so
-- every new artifact is a migration (the same churn 099 already fixed for
-- document *types*, for the same reason).
--
-- The shape here
-- --------------
--   onboarding_step_defs         the org's catalog: which steps, in what order,
--                                bundled how, signed by whom
--   onboarding_run_steps         per-run snapshot of that catalog + the live
--                                per-step state (replaces the status ladder)
--   onboarding_collect_items     an HR-defined checklist under a collect step
--   onboarding_collect_submissions  what the candidate actually uploaded
--
-- Steps come in three kinds. `generate` renders a template through the 099
-- pipeline (LOI, appointment letter, NDA, induction). `collect` asks the
-- candidate to upload files. `system` covers BGV references and policy
-- acknowledgement, whose logic stays where it is — only their position and
-- enabled-ness become catalog rows rather than JSONB booleans.
--
-- This migration is additive and inert. It creates the tables and backfills
-- them to describe what each org and each in-flight run is *already* doing;
-- the agent keeps dispatching off `onboarding_runs.status` until the engine is
-- switched on. Nothing here changes behaviour on its own.
--
-- Idempotent — every statement re-runnable.


-- ── 1. onboarding_step_defs ────────────────────────────────────────────────
-- The org-level catalog. One row per step the org runs, ordered by `position`.
--
-- `document_type_key` is a plain key, not a foreign key: `document_types.key`
-- is unique only within a scope (partial uniques on org-owned vs system rows,
-- see 099), so there is no single column to reference. It is resolved at
-- generation time by templates.resolve_document_type(), which already prefers
-- an org's own type over the system one of the same name.

CREATE TABLE IF NOT EXISTS onboarding_step_defs (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- Stable slug the agent, router and UI address this step by. Unique per org.
  step_key           TEXT NOT NULL,
  kind               TEXT NOT NULL,
  label              TEXT NOT NULL,
  description        TEXT,

  -- generate-kind only: which document_types.key to render.
  document_type_key  TEXT,

  -- Steps sharing a bundle_key are asked, waited on and reviewed as one unit.
  -- NULL means the step stands alone.
  bundle_key         TEXT,
  bundle_label       TEXT,

  position           INTEGER NOT NULL,
  enabled            BOOLEAN NOT NULL DEFAULT TRUE,

  -- Which parties must sign the artifact this step produces. '{}' means the
  -- document is sent, not signed. Order is routing order: signer_roles[1]
  -- signs before signer_roles[2]. generate-kind only.
  signer_roles       TEXT[] NOT NULL DEFAULT '{}',

  -- Structural steps the org may reorder but not delete or disable, because
  -- later steps read state they write. Only the LOI qualifies today.
  locked             BOOLEAN NOT NULL DEFAULT FALSE,

  -- Kind-specific settings. collect: {max_mb}. generate: render overrides.
  config             JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (org_id, step_key)
);

ALTER TABLE onboarding_step_defs DROP CONSTRAINT IF EXISTS onboarding_step_defs_kind_check;
ALTER TABLE onboarding_step_defs
  ADD CONSTRAINT onboarding_step_defs_kind_check CHECK (
    kind IN ('generate', 'collect', 'system')
  );

-- A generate step without a document type has nothing to render; a collect or
-- system step with one is a configuration mistake we'd rather catch on write.
ALTER TABLE onboarding_step_defs DROP CONSTRAINT IF EXISTS onboarding_step_defs_doc_type_check;
ALTER TABLE onboarding_step_defs
  ADD CONSTRAINT onboarding_step_defs_doc_type_check CHECK (
    (kind = 'generate' AND document_type_key IS NOT NULL)
    OR (kind <> 'generate' AND document_type_key IS NULL)
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_step_defs_org_position
  ON onboarding_step_defs (org_id, position);

ALTER TABLE onboarding_step_defs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS onboarding_step_defs_select ON onboarding_step_defs;
CREATE POLICY onboarding_step_defs_select ON onboarding_step_defs
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE onboarding_step_defs IS
  'Per-org onboarding step catalog: which steps run, in what order, grouped '
  'into which bundles, signed by whom. Replaces the four booleans in '
  'organizations.metadata.onboarding_v2_steps.';


-- ── 2. onboarding_collect_items ────────────────────────────────────────────
-- The checklist under a collect-kind step. Free-form: an org adds "10th
-- marksheet" or "Medical fitness certificate" as rows, not as code.

CREATE TABLE IF NOT EXISTS onboarding_collect_items (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  step_def_id      UUID NOT NULL REFERENCES onboarding_step_defs(id) ON DELETE CASCADE,

  item_key         TEXT NOT NULL,
  label            TEXT NOT NULL,
  help_text        TEXT,

  -- Only required items gate the run. An optional item can be skipped by the
  -- candidate without parking the pipeline.
  required         BOOLEAN NOT NULL DEFAULT TRUE,
  accepted_formats TEXT[] NOT NULL DEFAULT ARRAY['pdf', 'jpg', 'jpeg', 'png'],
  position         INTEGER NOT NULL DEFAULT 0,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (step_def_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_collect_items_step
  ON onboarding_collect_items (step_def_id, position);

ALTER TABLE onboarding_collect_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS onboarding_collect_items_select ON onboarding_collect_items;
CREATE POLICY onboarding_collect_items_select ON onboarding_collect_items
  FOR SELECT USING (org_id = auth_org_id());


-- ── 3. onboarding_run_steps ────────────────────────────────────────────────
-- The catalog as it stood when this run started, plus live per-step state.
--
-- Snapshotted rather than joined so a catalog edit cannot reorder or re-scope
-- a run already underway — a candidate must not be asked for a document that
-- appeared in the checklist after they finished uploading. `config` carries
-- the collect step's item list at snapshot time for the same reason.

CREATE TABLE IF NOT EXISTS onboarding_run_steps (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id         UUID NOT NULL REFERENCES onboarding_runs(id) ON DELETE CASCADE,

  -- Provenance only. The step def may be edited or deleted mid-run; every
  -- field this row needs is copied below, so nothing dereferences it.
  step_def_id    UUID REFERENCES onboarding_step_defs(id) ON DELETE SET NULL,

  step_key       TEXT NOT NULL,
  kind           TEXT NOT NULL,
  label          TEXT NOT NULL,
  document_type_key TEXT,
  bundle_key     TEXT,
  bundle_label   TEXT,
  position       INTEGER NOT NULL,
  signer_roles   TEXT[] NOT NULL DEFAULT '{}',
  config         JSONB NOT NULL DEFAULT '{}'::jsonb,

  status         TEXT NOT NULL DEFAULT 'pending',
  blocked_reason TEXT,

  started_at     TIMESTAMPTZ,
  completed_at   TIMESTAMPTZ,

  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (run_id, step_key)
);

ALTER TABLE onboarding_run_steps DROP CONSTRAINT IF EXISTS onboarding_run_steps_kind_check;
ALTER TABLE onboarding_run_steps
  ADD CONSTRAINT onboarding_run_steps_kind_check CHECK (
    kind IN ('generate', 'collect', 'system')
  );

-- Per-step lifecycle. Wider than any one kind uses: a generate step walks
-- generating → pending_hr_review → pending_signature → done, a collect step
-- active → done, a system step active → done. `skipped` is a disabled step
-- recorded in place, so a run's history shows what it did not do.
ALTER TABLE onboarding_run_steps DROP CONSTRAINT IF EXISTS onboarding_run_steps_status_check;
ALTER TABLE onboarding_run_steps
  ADD CONSTRAINT onboarding_run_steps_status_check CHECK (
    status IN (
      'pending', 'active', 'generating',
      'pending_hr_review', 'pending_signature',
      'submitted', 'pending_hr_approval',
      'blocked_missing_template', 'blocked_template_drift',
      'skipped', 'done', 'failed'
    )
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_run_steps_run_position
  ON onboarding_run_steps (run_id, position);
CREATE INDEX IF NOT EXISTS idx_onboarding_run_steps_bundle
  ON onboarding_run_steps (run_id, bundle_key) WHERE bundle_key IS NOT NULL;

ALTER TABLE onboarding_run_steps ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS onboarding_run_steps_select ON onboarding_run_steps;
CREATE POLICY onboarding_run_steps_select ON onboarding_run_steps
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE onboarding_run_steps IS
  'Per-run snapshot of onboarding_step_defs plus live per-step state. The '
  'granular state onboarding_runs.current_step only ever mirrored; '
  'onboarding_runs.status remains as a coarse run-level phase.';


-- ── 4. onboarding_collect_submissions ──────────────────────────────────────
-- One row per (run step, checklist item). A re-upload replaces the row rather
-- than accumulating history: the candidate correcting a blurry scan is not an
-- audit event, and the storage path is deterministic so the bytes are replaced
-- in place anyway.
--
-- `item_key` is denormalized rather than a foreign key into
-- onboarding_collect_items: the authoritative per-run checklist is the snapshot
-- in onboarding_run_steps.config, so an item HR deletes from the catalog
-- mid-run must not cascade away a submission the candidate already made.
--
-- review_status is deliberately NOT a gate. The run advances when every
-- required item is submitted; HR approving or rejecting a document happens
-- afterwards and never moves the pipeline.

CREATE TABLE IF NOT EXISTS onboarding_collect_submissions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id         UUID NOT NULL REFERENCES onboarding_runs(id) ON DELETE CASCADE,
  run_step_id    UUID NOT NULL REFERENCES onboarding_run_steps(id) ON DELETE CASCADE,

  item_key       TEXT NOT NULL,
  label          TEXT NOT NULL,

  storage_path   TEXT NOT NULL,
  original_filename TEXT,
  mime_type      TEXT,
  file_bytes     INTEGER,

  submitted_by   UUID REFERENCES users(id) ON DELETE SET NULL,
  submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  review_status  TEXT NOT NULL DEFAULT 'pending',
  reviewed_by    UUID REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at    TIMESTAMPTZ,
  review_note    TEXT,

  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (run_step_id, item_key)
);

ALTER TABLE onboarding_collect_submissions
  DROP CONSTRAINT IF EXISTS onboarding_collect_submissions_review_check;
ALTER TABLE onboarding_collect_submissions
  ADD CONSTRAINT onboarding_collect_submissions_review_check CHECK (
    review_status IN ('pending', 'approved', 'rejected')
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_collect_submissions_run
  ON onboarding_collect_submissions (run_id, run_step_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_collect_submissions_review
  ON onboarding_collect_submissions (org_id, review_status)
  WHERE review_status = 'pending';

ALTER TABLE onboarding_collect_submissions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS onboarding_collect_submissions_select ON onboarding_collect_submissions;
CREATE POLICY onboarding_collect_submissions_select ON onboarding_collect_submissions
  FOR SELECT USING (org_id = auth_org_id());


-- ── 5. onboarding_documents: own the step, not a fixed kind ────────────────
-- `kind` stops being a closed vocabulary. It stays as the human-readable slug
-- (and every existing query keyed on it keeps working), but validity now comes
-- from the step catalog rather than a CHECK nobody can extend without a
-- migration — the same move 099 made for document_types.

ALTER TABLE onboarding_documents
  DROP CONSTRAINT IF EXISTS onboarding_documents_kind_check;

ALTER TABLE onboarding_documents
  ADD COLUMN IF NOT EXISTS run_step_id UUID REFERENCES onboarding_run_steps(id) ON DELETE SET NULL;

-- Per-signer state for arbitrary signer lists, e.g.
-- {"hr": "signed", "candidate": "pending"}. `sign_status` remains the coarse
-- lifecycle the existing UI and esign adapter read; this is the detail it
-- cannot express once signer_roles is configurable.
ALTER TABLE onboarding_documents
  ADD COLUMN IF NOT EXISTS signer_progress JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_onboarding_documents_run_step
  ON onboarding_documents (run_step_id) WHERE run_step_id IS NOT NULL;

COMMENT ON COLUMN onboarding_documents.kind IS
  'Step slug this artifact belongs to (loi, appointment_letter, nda, '
  'offer_bundle, induction, or any org-defined step_key). No longer CHECK-'
  'constrained — see onboarding_step_defs.';


-- ── 6. updated_at triggers ─────────────────────────────────────────────────

DROP TRIGGER IF EXISTS trg_onboarding_step_defs_updated_at ON onboarding_step_defs;
CREATE TRIGGER trg_onboarding_step_defs_updated_at
  BEFORE UPDATE ON onboarding_step_defs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_onboarding_collect_items_updated_at ON onboarding_collect_items;
CREATE TRIGGER trg_onboarding_collect_items_updated_at
  BEFORE UPDATE ON onboarding_collect_items
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_onboarding_run_steps_updated_at ON onboarding_run_steps;
CREATE TRIGGER trg_onboarding_run_steps_updated_at
  BEFORE UPDATE ON onboarding_run_steps
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_onboarding_collect_submissions_updated_at ON onboarding_collect_submissions;
CREATE TRIGGER trg_onboarding_collect_submissions_updated_at
  BEFORE UPDATE ON onboarding_collect_submissions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── 7. Backfill: describe what every org is already doing ──────────────────
-- Six catalog rows per org, reproducing the hardcoded pipeline exactly.
-- `enabled` comes from the org's existing four booleans; an org that never
-- configured them gets all-on, which is what _parse_steps() already defaults to.
--
-- Signer roles reproduce today's behaviour rather than an idealised one: the
-- LOI is routed HR → candidate, the appointment bundle is candidate-only (see
-- the envelope descriptions in 081), and the induction pack is sent unsigned.
--
-- ON CONFLICT DO NOTHING throughout: re-running must not clobber a catalog an
-- org has since edited.

INSERT INTO onboarding_step_defs (
  org_id, step_key, kind, label, description, document_type_key,
  bundle_key, bundle_label, position, enabled, signer_roles, locked
)
SELECT
  o.id,
  d.step_key,
  d.kind,
  d.label,
  d.description,
  d.document_type_key,
  d.bundle_key,
  d.bundle_label,
  d.position,
  CASE d.toggle_key
    WHEN '' THEN TRUE
    ELSE COALESCE(
      (o.metadata -> 'onboarding_v2_steps' ->> d.toggle_key)::boolean,
      TRUE
    )
  END,
  d.signer_roles,
  d.locked
FROM organizations o
CROSS JOIN (
  VALUES
    ('loi', 'generate', 'Letter of intent',
     'Generate the LOI, route it for signature, and send it to the candidate.',
     'letter_of_intent', NULL, NULL, 0, '', ARRAY['hr', 'candidate'], TRUE),
    ('bgv', 'system', 'Background verification',
     'Ask the candidate for references, then email each one a verification form.',
     NULL, NULL, NULL, 10, 'bgv', ARRAY[]::text[], FALSE),
    ('appointment_letter', 'generate', 'Appointment letter',
     'Generate the appointment letter for HR review.',
     'appointment_letter', 'appointment_bundle', 'Appointment letter + NDA',
     20, 'appointment_bundle', ARRAY['candidate'], FALSE),
    ('nda', 'generate', 'NDA',
     'Generate the NDA for HR review.',
     'nda', 'appointment_bundle', 'Appointment letter + NDA',
     21, 'appointment_bundle', ARRAY['candidate'], FALSE),
    ('policies', 'system', 'Policy acknowledgement',
     'Assign policies that need signing off and wait for the candidate to acknowledge.',
     NULL, NULL, NULL, 30, 'policies', ARRAY[]::text[], FALSE),
    ('induction', 'generate', 'Induction',
     'Generate the induction pack and email it to the candidate.',
     'induction', NULL, NULL, 40, 'induction', ARRAY[]::text[], FALSE)
) AS d (
  step_key, kind, label, description, document_type_key,
  bundle_key, bundle_label, position, toggle_key, signer_roles, locked
)
ON CONFLICT (org_id, step_key) DO NOTHING;


-- ── 8. Backfill: place every in-flight run on its catalog ──────────────────
-- Reconstructs per-step state from the single legacy status string. Steps
-- before the run's current one are `done`, the current one carries the status
-- that maps to it, later ones are `pending`, and a disabled step is `skipped`.
--
-- The reconstruction is necessarily coarse — one status string cannot say
-- whether *this* run skipped BGV or completed it, only what the org's toggles
-- say today. That matches what the agent already does (it re-reads the org's
-- toggles on every kick and has never pinned them per run), so this records
-- the behaviour that is already in effect rather than inventing one.
--
-- `completed` runs are backfilled so the timeline UI can render their history.
-- `failed` and `cancelled` are not: their status says nothing about how far
-- they got, and guessing would write a history that never happened. Nothing
-- dispatches on them either way — run() returns early on terminal states.

WITH progress AS (
  -- Which step each legacy status sits on, and that step's state. The
  -- position everything before it is `done` past is read from the catalog
  -- below rather than hardcoded here, so a reordered catalog stays consistent.
  SELECT * FROM (
    VALUES
      ('draft',                          'loi',                'generating'),
      ('loi_generating',                 'loi',                'generating'),
      ('loi_pending_hr_review',          'loi',                'pending_hr_review'),
      ('loi_pending_hr_sign',            'loi',                'pending_signature'),
      ('loi_pending_esign_signature',    'loi',                'pending_signature'),
      ('loi_signed_uploaded',            'loi',                'active'),
      ('loi_sent_to_candidate',          'bgv',                'pending'),
      ('awaiting_candidate_references',  'bgv',                'active'),
      ('bgv_pending',                    'bgv',                'active'),
      ('bgv_complete',                   'appointment_letter', 'pending'),
      ('appointment_bundle_generating',  'appointment_letter', 'generating'),
      ('appointment_pending_hr_review',  'appointment_letter', 'pending_hr_review'),
      ('appointment_sent_to_candidate',  'policies',           'pending'),
      ('policies_assigned',              'policies',           'active'),
      ('policies_acknowledged',          'induction',          'pending'),
      ('induction_generating',           'induction',          'generating'),
      ('induction_sent',                 'induction',          'done'),
      ('completed',                      'induction',          'done')
  ) AS p (legacy_status, current_step_key, current_step_status)
),
placed AS (
  SELECT
    r.id           AS run_id,
    r.org_id,
    -- Blocked runs report which template stalled them; that step is the
    -- current one, and the blocked statuses are deliberately absent from
    -- `progress` because the ladder alone cannot say where they stalled.
    COALESCE(
      CASE WHEN r.status IN ('blocked_missing_template', 'blocked_template_drift')
           THEN NULLIF(LOWER(r.blocked_template_kind), '') END,
      p.current_step_key
    ) AS current_step_key,
    CASE WHEN r.status IN ('blocked_missing_template', 'blocked_template_drift')
         THEN r.status ELSE p.current_step_status END AS current_step_status,
    r.blocked_reason
  FROM onboarding_runs r
  LEFT JOIN progress p ON p.legacy_status = r.status
  WHERE r.status NOT IN ('failed', 'cancelled')
),
anchored AS (
  -- Resolve the current step's position from the org's own catalog. A blocked
  -- run whose blocked_template_kind is missing drops out here rather than
  -- being placed at a guessed position; it re-materializes on its next kick.
  SELECT pl.*, cur.position AS current_position
  FROM placed pl
  JOIN onboarding_step_defs cur
    ON cur.org_id = pl.org_id AND cur.step_key = pl.current_step_key
  WHERE pl.current_step_key IS NOT NULL
)
INSERT INTO onboarding_run_steps (
  org_id, run_id, step_def_id, step_key, kind, label, document_type_key,
  bundle_key, bundle_label, position, signer_roles, status, blocked_reason
)
SELECT
  pl.org_id,
  pl.run_id,
  sd.id,
  sd.step_key,
  sd.kind,
  sd.label,
  sd.document_type_key,
  sd.bundle_key,
  sd.bundle_label,
  sd.position,
  sd.signer_roles,
  CASE
    WHEN NOT sd.enabled                       THEN 'skipped'
    WHEN sd.step_key = pl.current_step_key    THEN pl.current_step_status
    -- Bundle siblings share the state of whichever member the ladder named
    -- (the ladder only ever named appointment_letter for the AL+NDA pair).
    WHEN sd.bundle_key IS NOT NULL AND sd.bundle_key = (
           SELECT s2.bundle_key FROM onboarding_step_defs s2
           WHERE s2.org_id = pl.org_id AND s2.step_key = pl.current_step_key
         )                                    THEN pl.current_step_status
    WHEN sd.position < pl.current_position     THEN 'done'
    ELSE 'pending'
  END,
  CASE WHEN sd.step_key = pl.current_step_key THEN pl.blocked_reason END
FROM anchored pl
JOIN onboarding_step_defs sd ON sd.org_id = pl.org_id
ON CONFLICT (run_id, step_key) DO NOTHING;


-- Point existing artifacts at the step that owns them. `kind` matched
-- `step_key` for every document the hardcoded pipeline ever produced, except
-- the merged offer_bundle PDF, which belongs to no single step.
UPDATE onboarding_documents od
SET run_step_id = rs.id
FROM onboarding_run_steps rs
WHERE rs.run_id = od.run_id
  AND rs.step_key = od.kind
  AND od.run_step_id IS NULL;
