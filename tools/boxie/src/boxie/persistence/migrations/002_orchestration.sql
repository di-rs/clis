ALTER TABLE outbox DROP CONSTRAINT outbox_kind_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_kind_check CHECK (kind IN ('start','stop','observation'));
ALTER TABLE orchestrator_observations ADD COLUMN attachment_epoch bigint NOT NULL DEFAULT 0;
ALTER TABLE orchestrator_observations ADD COLUMN absolute_deadline timestamptz;
ALTER TABLE orchestrator_observations ADD COLUMN control_lease_until timestamptz;
ALTER TABLE orchestrator_observations ADD COLUMN container_id text;
