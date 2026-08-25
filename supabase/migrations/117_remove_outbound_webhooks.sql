-- Remove the deferred customer-facing outbound webhook feature.
-- Delivery rows reference webhook configurations, so drop them first.

DROP TABLE IF EXISTS webhook_deliveries;
DROP TABLE IF EXISTS webhooks;
