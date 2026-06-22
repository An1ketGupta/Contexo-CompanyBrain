-- ── Production Roadmap Day 6 — Stripe Billing Foundation
--
-- Three things in one migration because they ship together:
--
--   1. organizations: billing columns (stripe_customer_id, plan, plan_status,
--      seat_count, trial_ends_at, cancel_at_period_end, current_period_end,
--      plan_updated_at). Tightened CHECK constraints on plan and plan_status
--      so a typo in a webhook handler can't silently corrupt billing state.
--      A BEFORE UPDATE trigger keeps plan_updated_at honest.
--
--   2. pricing_tiers: canonical mapping from Stripe price_id → plan +
--      quotas. Reasons we don't keep this only in env vars:
--        * Test/Live cutover becomes one row-set swap, not a 6-variable env
--          replay (the variable footprint roughly doubles when annual prices
--          are introduced).
--        * Quotas live next to the plan they apply to — no chasing
--          constants across a config file and a router.
--        * The webhook handler doesn't need redeploy when a plan's quota
--          changes; ops can update the row directly.
--
--   3. billing_events: write-ahead log for Stripe webhooks. We persist the
--      raw payload BEFORE processing so a handler crash mid-event leaves
--      forensic evidence. Idempotency is enforced by UNIQUE(stripe_event_id).
--
-- All three are additive and reversible. No data loss on rollback.

BEGIN;

-- ── 1. organizations billing columns ────────────────────────────────────────

-- The original CHECK allowed ('free', 'starter', 'growth', 'business'). We
-- renamed 'growth' to 'team' to match PRICING.md / CLAUDE.md. Migrate any
-- legacy rows BEFORE we install the new constraint.
UPDATE organizations SET plan = 'team' WHERE plan = 'growth';

ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_plan_check;

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS stripe_customer_id      TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id  TEXT UNIQUE,
  -- 'free' kept for backwards compatibility with existing rows. New orgs
  -- created post-launch still default here until they complete Checkout —
  -- there is no public free tier, but the plan column must hold *some*
  -- value before the subscription event lands.
  ADD COLUMN IF NOT EXISTS plan_status             TEXT NOT NULL DEFAULT 'inactive',
  ADD COLUMN IF NOT EXISTS plan_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS current_period_end      TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS trial_ends_at           TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cancel_at_period_end    BOOLEAN NOT NULL DEFAULT FALSE,
  -- Seat count maps to Stripe's `quantity` on the subscription item; for
  -- per-seat pricing this is the source of truth. NULL when irrelevant
  -- (current tiers are flat, not per-seat — leaving the column nullable
  -- avoids forcing a sentinel value when a tier doesn't use it).
  ADD COLUMN IF NOT EXISTS seat_count              INT;

-- Plan column already exists with a CHECK from migration 001. Re-add with
-- the new allowed set. NOTE: this is permissive on purpose — Stripe
-- webhook handlers should map unknown price IDs to 'free' rather than
-- inserting a sentinel that fails the CHECK.
ALTER TABLE organizations
  ADD CONSTRAINT organizations_plan_check
    CHECK (plan IN ('free', 'starter', 'team', 'business'));

-- plan_status mirrors Stripe Subscription.status verbatim (plus 'inactive'
-- for orgs that have never had a subscription). Keeping the names aligned
-- means the webhook handler can pass status straight through.
ALTER TABLE organizations
  ADD CONSTRAINT organizations_plan_status_check
    CHECK (plan_status IN (
      'inactive',         -- never subscribed (default for free-tier orgs)
      'trialing',
      'active',
      'past_due',
      'canceled',
      'unpaid',
      'incomplete',
      'incomplete_expired',
      'paused'
    ));

CREATE INDEX IF NOT EXISTS idx_organizations_stripe_customer_id
  ON organizations(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_organizations_plan_status
  ON organizations(plan_status)
  WHERE plan_status IN ('past_due', 'unpaid');

COMMENT ON COLUMN organizations.plan IS
  'free | starter | team | business — denormalized cache of Stripe subscription, rebuilt by webhook. "free" = no active sub.';
COMMENT ON COLUMN organizations.plan_status IS
  'Mirrors Stripe Subscription.status, plus "inactive" for never-subscribed orgs.';
COMMENT ON COLUMN organizations.plan_updated_at IS
  'Maintained automatically by the bump_organizations_plan_updated_at trigger.';


-- ── 1b. Auto-update plan_updated_at on any billing-state change ─────────────
-- Relying on every webhook handler to set this manually is a future bug
-- waiting to happen. Centralize it.

CREATE OR REPLACE FUNCTION bump_organizations_plan_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF (NEW.plan IS DISTINCT FROM OLD.plan)
     OR (NEW.plan_status IS DISTINCT FROM OLD.plan_status)
     OR (NEW.stripe_subscription_id IS DISTINCT FROM OLD.stripe_subscription_id)
     OR (NEW.current_period_end IS DISTINCT FROM OLD.current_period_end)
     OR (NEW.cancel_at_period_end IS DISTINCT FROM OLD.cancel_at_period_end)
     OR (NEW.seat_count IS DISTINCT FROM OLD.seat_count) THEN
    NEW.plan_updated_at := now();
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS organizations_bump_plan_updated_at ON organizations;
CREATE TRIGGER organizations_bump_plan_updated_at
  BEFORE UPDATE ON organizations
  FOR EACH ROW
  EXECUTE FUNCTION bump_organizations_plan_updated_at();


-- ── 2. pricing_tiers ────────────────────────────────────────────────────────
-- One row per (plan, interval, stripe_mode). The seed script
-- `scripts/stripe_seed.py` populates this; the webhook handler reads
-- `stripe_price_id → plan` from it.

CREATE TABLE IF NOT EXISTS pricing_tiers (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  plan                   TEXT NOT NULL,           -- 'starter' | 'team' | 'business'
  interval               TEXT NOT NULL,           -- 'month' | 'year'
  stripe_mode            TEXT NOT NULL,           -- 'test' | 'live'

  stripe_product_id      TEXT NOT NULL,
  stripe_price_id        TEXT NOT NULL,
  unit_amount_cents      INTEGER NOT NULL CHECK (unit_amount_cents >= 0),
  currency               TEXT NOT NULL DEFAULT 'usd',

  -- Quotas at this tier. NULL = unlimited (Business tier). Keeping these
  -- on the tier row (vs. a hardcoded dict in Python) means ops can
  -- raise/lower a tier's limits without a redeploy.
  quota_documents        INTEGER,
  quota_queries_monthly  INTEGER,
  quota_seats            INTEGER,

  is_active              BOOLEAN NOT NULL DEFAULT TRUE,

  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT pricing_tiers_plan_check
    CHECK (plan IN ('starter', 'team', 'business')),
  CONSTRAINT pricing_tiers_interval_check
    CHECK (interval IN ('month', 'year')),
  CONSTRAINT pricing_tiers_stripe_mode_check
    CHECK (stripe_mode IN ('test', 'live'))
);

-- The webhook hot-path lookup. stripe_price_id is naturally unique per
-- Stripe environment; combining with stripe_mode gives us the global
-- uniqueness we need to safely host test+live rows side-by-side.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pricing_tiers_price
  ON pricing_tiers(stripe_price_id, stripe_mode);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pricing_tiers_plan_interval_mode
  ON pricing_tiers(plan, interval, stripe_mode)
  WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_pricing_tiers_plan_mode
  ON pricing_tiers(plan, stripe_mode);

ALTER TABLE pricing_tiers ENABLE ROW LEVEL SECURITY;

-- Publicly readable — these are list prices, not secrets. The /pricing page
-- on marketing surfaces will hit this table.
DROP POLICY IF EXISTS pricing_tiers_select_public ON pricing_tiers;
CREATE POLICY pricing_tiers_select_public ON pricing_tiers
  FOR SELECT USING (is_active = TRUE);

-- Writes are service-role only (seed script + admin endpoints).

COMMENT ON TABLE pricing_tiers IS
  'Canonical Stripe Price → plan + quotas mapping. Seeded by scripts/stripe_seed.py. Read by the webhook handler and the /pricing page.';
COMMENT ON COLUMN pricing_tiers.quota_documents IS
  'NULL = unlimited. The webhook handler caches this onto the org for fast quota checks.';


-- ── 3. billing_events ──────────────────────────────────────────────────────
-- Stripe webhook write-ahead log. Every received event lands here BEFORE
-- the handler runs its side-effects. Status transitions: received →
-- processed | failed | duplicate.
--
-- Why we don't reuse analytics_events: Stripe events can be retried for
-- days, so the idempotency UNIQUE constraint is load-bearing — putting it
-- on a high-cardinality general table would hurt write throughput
-- everywhere. A dedicated table also keeps PCI-relevant payloads
-- (last4, customer email) out of the general analytics dataset.

CREATE TABLE IF NOT EXISTS billing_events (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  stripe_event_id    TEXT NOT NULL UNIQUE,
  event_type         TEXT NOT NULL,
  -- ON DELETE SET NULL: if an org is hard-deleted we keep the audit row
  -- (compliance + reconciliation matter more than referential cleanliness).
  org_id             UUID REFERENCES organizations(id) ON DELETE SET NULL,
  stripe_customer_id TEXT,
  payload            JSONB NOT NULL,
  status             TEXT NOT NULL DEFAULT 'received',
  error_message      TEXT,
  received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at       TIMESTAMPTZ,

  CONSTRAINT billing_events_status_check
    CHECK (status IN ('received', 'processed', 'failed', 'duplicate'))
);

CREATE INDEX IF NOT EXISTS idx_billing_events_org
  ON billing_events(org_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_events_customer
  ON billing_events(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_billing_events_type_status
  ON billing_events(event_type, status, received_at DESC);
-- Spot-check failed events fast for admin paging-through.
CREATE INDEX IF NOT EXISTS idx_billing_events_failed
  ON billing_events(received_at DESC)
  WHERE status = 'failed';

ALTER TABLE billing_events ENABLE ROW LEVEL SECURITY;

-- No public SELECT/INSERT/UPDATE policy. Only the webhook handler
-- (service-role) and admin endpoints touch this table. Admin UI reads
-- happen through a server route that uses service-role itself.

COMMENT ON TABLE billing_events IS
  'Day 6: Stripe webhook write-ahead log. Insert on receipt, transition to processed/failed.';
COMMENT ON COLUMN billing_events.payload IS
  'Full Stripe event payload as JSON. Trimmed for size only if Stripe ships a payload >1 MB (none currently do).';


-- ── 4. Backfill — existing orgs ─────────────────────────────────────────────
-- Existing rows already default to plan='free' from migration 001. Make
-- sure plan_status is set on every legacy row (NOT NULL DEFAULT 'inactive'
-- already handles this for ADD COLUMN; this is belt + braces).
UPDATE organizations
SET plan_status = 'inactive'
WHERE plan_status IS NULL;

COMMIT;
