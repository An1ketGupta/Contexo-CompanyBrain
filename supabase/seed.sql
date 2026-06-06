-- Development seed — creates a test organization
-- Run after migrations: supabase db push && supabase db seed

INSERT INTO organizations (id, name, slug, plan)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'Acme Corp (Dev)',
  'acme-dev',
  'business'
) ON CONFLICT (slug) DO NOTHING;
