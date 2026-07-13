-- ── 054 — Custom Persona Builder (Feature 1.11)
--
-- Extends the persona system from 6 hardcoded roles to user-defined and
-- org-defined personas. Three additive moves:
--
-- 1. Widen users.persona to accept the literal value 'custom' on top of the
--    existing 6 built-ins. When persona='custom' the overlay text comes from
--    users.custom_persona_instructions instead of the in-code dict.
--
-- 2. Add users.custom_persona_name + users.custom_persona_instructions for
--    per-user personal personas.
--
-- 3. New org_personas table: admin-curated personas every user in the org
--    can pick from. Migration deliberately stops short of FK'ing
--    users.persona to org_personas — the persona slot remains a freeform
--    text value with a CHECK constraint so the hot read path stays a single
--    column without a JOIN.

-- ── 1. Widen users.persona ──────────────────────────────────────────────────
-- Drop and re-create the CHECK constraint to allow 'custom' plus an
-- "org:<uuid>" form for org-personas. Validation:
--   • NULL                                — unset
--   • 'hr' | 'sales' | … | 'executive'    — built-ins (back compat)
--   • 'custom'                            — read from users.custom_persona_*
--   • 'org:<uuid>'                        — read from org_personas by id
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_persona_check;
ALTER TABLE users
  ADD CONSTRAINT users_persona_check CHECK (
    persona IS NULL
    OR persona IN ('hr', 'sales', 'engineering', 'finance', 'operations', 'executive', 'custom')
    OR persona ~ '^org:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  );

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS custom_persona_name TEXT,
  ADD COLUMN IF NOT EXISTS custom_persona_instructions TEXT;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_custom_persona_name_len;
ALTER TABLE users
  ADD CONSTRAINT users_custom_persona_name_len
  CHECK (custom_persona_name IS NULL OR char_length(custom_persona_name) BETWEEN 1 AND 80);

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_custom_persona_instructions_len;
ALTER TABLE users
  ADD CONSTRAINT users_custom_persona_instructions_len
  CHECK (
    custom_persona_instructions IS NULL
    OR char_length(custom_persona_instructions) BETWEEN 10 AND 2000
  );

COMMENT ON COLUMN users.custom_persona_name IS
  'Feature 1.11: human-readable label for a user-defined custom persona. Shown in the persona picker. Only meaningful when persona = ''custom''.';
COMMENT ON COLUMN users.custom_persona_instructions IS
  'Feature 1.11: system-prompt overlay text used when persona = ''custom''. Capped at 2000 chars to keep prompt budget bounded.';


-- ── 2. Org-curated personas ─────────────────────────────────────────────────
-- Admins define shared personas (e.g. "Customer Success", "Legal"). Users
-- adopt them by setting users.persona = 'org:<id>'. Soft-delete via
-- is_archived because flipping a persona off shouldn't break existing users
-- — the chain falls back to default behavior cleanly via _persona_overlay().
CREATE TABLE IF NOT EXISTS org_personas (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by    UUID NOT NULL REFERENCES users(id),
  name          TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  description   TEXT CHECK (description IS NULL OR char_length(description) <= 240),
  instructions  TEXT NOT NULL CHECK (char_length(instructions) BETWEEN 10 AND 2000),
  is_archived   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS idx_org_personas_org
  ON org_personas (org_id) WHERE is_archived = FALSE;

CREATE OR REPLACE FUNCTION org_personas_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_org_personas_touch_updated_at ON org_personas;
CREATE TRIGGER trg_org_personas_touch_updated_at
  BEFORE UPDATE ON org_personas
  FOR EACH ROW EXECUTE FUNCTION org_personas_touch_updated_at();

ALTER TABLE org_personas ENABLE ROW LEVEL SECURITY;

-- Every org member can read non-archived personas (they need to pick them).
DROP POLICY IF EXISTS "org_personas_select" ON org_personas;
CREATE POLICY "org_personas_select" ON org_personas
  FOR SELECT USING (org_id = auth_org_id());

-- Admins-only for writes.
DROP POLICY IF EXISTS "org_personas_insert_admin" ON org_personas;
CREATE POLICY "org_personas_insert_admin" ON org_personas
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND created_by = auth.uid()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );

DROP POLICY IF EXISTS "org_personas_update_admin" ON org_personas;
CREATE POLICY "org_personas_update_admin" ON org_personas
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );

DROP POLICY IF EXISTS "org_personas_delete_admin" ON org_personas;
CREATE POLICY "org_personas_delete_admin" ON org_personas
  FOR DELETE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );

COMMENT ON TABLE org_personas IS
  'Feature 1.11: admin-curated personas users can adopt by setting users.persona = ''org:<id>''. Replaces ad-hoc text overlays with versioned, shareable role profiles.';
