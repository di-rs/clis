CREATE TABLE artifacts (
    id uuid PRIMARY KEY,
    consumer_id text NOT NULL,
    digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    storage_id uuid NOT NULL UNIQUE,
    retired boolean NOT NULL DEFAULT false,
    collection_pending boolean NOT NULL DEFAULT false,
    collected_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE sandboxes (
    id uuid PRIMARY KEY,
    consumer_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    spec jsonb NOT NULL,
    snapshot jsonb NOT NULL,
    installation_id uuid NOT NULL,
    host_id text NOT NULL,
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    agent_boot_id uuid,
    accepted_at timestamptz NOT NULL,
    absolute_deadline timestamptz NOT NULL,
    desired_stop boolean NOT NULL DEFAULT false,
    reason text,
    state text NOT NULL DEFAULT 'accepted' CHECK (state IN
        ('accepted', 'provisioning', 'ready', 'terminating', 'terminated', 'failed')),
    cleanup_pending boolean NOT NULL DEFAULT false,
    cleaned_at timestamptz,
    UNIQUE (consumer_id, idempotency_key),
    CHECK (absolute_deadline > accepted_at AND absolute_deadline <= accepted_at + interval '1 hour'),
    CHECK (cleaned_at IS NULL OR (NOT cleanup_pending AND state IN ('terminated', 'failed')))
);
CREATE TABLE capacity_slots (
    slot integer PRIMARY KEY CHECK (slot BETWEEN 1 AND 8),
    sandbox_id uuid UNIQUE REFERENCES sandboxes(id) ON DELETE RESTRICT
);
INSERT INTO capacity_slots (slot) SELECT generate_series(1, 8);
CREATE TABLE sandbox_artifact_refs (
    sandbox_id uuid PRIMARY KEY REFERENCES sandboxes(id) ON DELETE RESTRICT,
    artifact_id uuid NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT
);
CREATE INDEX artifact_references ON sandbox_artifact_refs (artifact_id);
CREATE TABLE outbox (
    id uuid PRIMARY KEY,
    deduplication_key text NOT NULL UNIQUE,
    sandbox_id uuid NOT NULL REFERENCES sandboxes(id) ON DELETE RESTRICT,
    kind text NOT NULL CHECK (kind IN ('start', 'stop')),
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_owner text,
    lease_expires_at timestamptz,
    delivered_at timestamptz,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX outbox_pending ON outbox (next_attempt_at) WHERE delivered_at IS NULL;
CREATE TABLE agent_observations (
    sandbox_id uuid PRIMARY KEY REFERENCES sandboxes(id) ON DELETE RESTRICT,
    generation bigint NOT NULL,
    boot_id uuid NOT NULL,
    event_version bigint NOT NULL CHECK (event_version > 0),
    observed_at timestamptz NOT NULL,
    ready_at timestamptz,
    last_input_at timestamptz,
    disconnected_at timestamptz,
    resource_present boolean NOT NULL,
    removal_confirmed boolean NOT NULL DEFAULT false,
    cleanup_pending boolean NOT NULL DEFAULT false,
    CHECK (NOT removal_confirmed OR (NOT resource_present AND NOT cleanup_pending))
);
