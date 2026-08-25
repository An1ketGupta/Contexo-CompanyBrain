-- Remove the retired per-organization embedding fine-tuning subsystem.
-- Standard provider-backed embeddings and the chunks.embedding column remain.

UPDATE organizations
SET metadata = COALESCE(metadata, '{}'::jsonb)
  - 'embedding_model'
  - 'embedding_fine_tuned_at'
WHERE metadata ? 'embedding_model'
   OR metadata ? 'embedding_fine_tuned_at';

DROP TABLE IF EXISTS embedding_fine_tune_jobs;
DROP TABLE IF EXISTS embedding_training_pairs;
