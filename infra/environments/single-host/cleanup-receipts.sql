-- Run with psql -XqAt -v ON_ERROR_STOP=1 after stopping all application writers.
BEGIN TRANSACTION READ ONLY;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM sandboxes WHERE state NOT IN ('terminated', 'failed')
             OR cleaned_at IS NULL OR cleanup_pending)
     OR EXISTS (SELECT 1 FROM capacity_slots WHERE sandbox_id IS NOT NULL)
     OR EXISTS (SELECT 1 FROM sandbox_artifact_refs)
     OR EXISTS (SELECT 1 FROM outbox WHERE delivered_at IS NULL)
     OR EXISTS (SELECT 1 FROM artifacts WHERE collection_pending)
  THEN RAISE EXCEPTION 'drain sandboxes and pending operations before exporting receipts';
  END IF;
END $$;
SELECT coalesce(json_agg(json_build_object(
  'view', json_build_object(
    'id', id, 'state', state, 'created_at', accepted_at,
    'absolute_deadline', absolute_deadline, 'reason', reason,
    'cleanup_pending', cleanup_pending),
  'cleaned_at', cleaned_at)), '[]'::json)
FROM sandboxes WHERE consumer_id IN ('playground', 'cli-playground');
COMMIT;
