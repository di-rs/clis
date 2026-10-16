ALTER TABLE sandboxes
 ADD COLUMN cleanup_pending_since timestamptz,
 ADD COLUMN provisioning_outcome text CHECK (provisioning_outcome IN ('ready','failed','stopped')),
 ADD COLUMN provisioning_ready_observed_at timestamptz,
 ADD CONSTRAINT provisioning_ready_evidence CHECK (
   (provisioning_outcome = 'ready' AND provisioning_ready_observed_at IS NOT NULL)
   OR (provisioning_outcome IS DISTINCT FROM 'ready' AND provisioning_ready_observed_at IS NULL)
 );
