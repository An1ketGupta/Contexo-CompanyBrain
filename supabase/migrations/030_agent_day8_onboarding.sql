-- ── Agent Roadmap Day 8 — New Employee Onboarding Autoflow
--
-- When a user accepts an org invite, an autonomous agent fires:
--   1. Generates a personalised onboarding plan via execute_task
--   2. Creates a Notion page (if Notion connected)
--   3. Sends a welcome email
--   4. DMs the manager on Slack with the plan link
--
-- New columns:
--   * invitations.manager_user_id — admin picks a manager when creating
--     the invite. The agent uses this to send the manager DM in step 4.
--     Optional — if NULL, step 4 is skipped.
--   * users.onboarding_status — 'not_started' | 'plan_generated' |
--     'completed'. Status surfaces in the team activity panel.
--   * users.onboarding_agent_run_id — links the audit row from Day 7's
--     agent_runs table so an admin can drill into "what did the agent
--     do for this hire" from the team management page.
--   * onboarding_plans — one row per hire; persists the plan text +
--     Notion link so we can show "Your onboarding plan" in the new
--     hire's dashboard without re-running the agent.

ALTER TABLE invitations
  ADD COLUMN IF NOT EXISTS manager_user_id UUID
    REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS role_title TEXT,
  ADD COLUMN IF NOT EXISTS start_date DATE;

COMMENT ON COLUMN invitations.manager_user_id IS
  'Day 8: optional manager who gets a Slack DM when the new hire joins.';
COMMENT ON COLUMN invitations.role_title IS
  'Day 8: free-text role (e.g. "Senior Engineer"). Used by the onboarding agent prompt.';
COMMENT ON COLUMN invitations.start_date IS
  'Day 8: optional start date — drives the welcome email + plan personalisation.';


ALTER TABLE users
  ADD COLUMN IF NOT EXISTS onboarding_status TEXT NOT NULL DEFAULT 'not_started',
  ADD COLUMN IF NOT EXISTS onboarding_agent_run_id UUID
    REFERENCES agent_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS role_title TEXT,
  ADD COLUMN IF NOT EXISTS start_date DATE,
  ADD COLUMN IF NOT EXISTS manager_user_id UUID
    REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE users
  DROP CONSTRAINT IF EXISTS users_onboarding_status_check;
ALTER TABLE users
  ADD CONSTRAINT users_onboarding_status_check
    CHECK (onboarding_status IN ('not_started', 'in_progress', 'plan_generated', 'completed', 'failed'));


CREATE TABLE IF NOT EXISTS onboarding_plans (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_run_id             UUID REFERENCES agent_runs(id) ON DELETE SET NULL,

  plan_content             TEXT NOT NULL,
  plan_sources             JSONB NOT NULL DEFAULT '[]'::jsonb,

  notion_page_id           TEXT,
  notion_page_url          TEXT,

  welcome_email_sent_at    TIMESTAMPTZ,
  manager_notified_at      TIMESTAMPTZ,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One plan per user — the agent should never produce two.
CREATE UNIQUE INDEX IF NOT EXISTS uq_onboarding_plans_user
  ON onboarding_plans(user_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_plans_org_created
  ON onboarding_plans(org_id, created_at DESC);


ALTER TABLE onboarding_plans ENABLE ROW LEVEL SECURITY;

-- The new hire sees their own plan. Admins see everything.
DROP POLICY IF EXISTS onboarding_plans_select_self_or_admin ON onboarding_plans;
CREATE POLICY onboarding_plans_select_self_or_admin ON onboarding_plans
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      user_id = auth.uid()
      OR EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
    )
  );


COMMENT ON TABLE onboarding_plans IS
  'Day 8: persisted onboarding plan per new hire, linked to the agent_runs audit row.';
