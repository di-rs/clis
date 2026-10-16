# Boxie ORM and Temporal simplification

Approved scope: refactor PR #40 and push to its existing `feat/boxie-temporal` branch.

Use SQLModel table models with SQLAlchemy async sessions backed by psycopg. All
Boxie application queries, writes and database test setup use mapped models and
expression APIs; never execute handwritten SQL. Preserve transactional admission,
eight capacity slots, immutable idempotency, artifact retention, row locking,
outbox leases and boot/generation fencing. One async session belongs to one task.

Alembic owns Boxie schema revisions, with explicit upgrade jobs and packaged
migration sources. Replace the unreleased numbered SQL migrations and checksum
runner with an initial revision using Alembic operations and SQLAlchemy schema
expressions. Seed the eight slots through mapped instances. API startup does not
migrate. Temporal's own database schema remains owned by its upstream tooling.

Simplify workflows with typed activity references and native retry policies,
durable timers and cancellation. Stop must commit before removal, and cleanup
must remain durable until physical absence is confirmed in PostgreSQL, including
when a racing fence rejects an RPC report. After durable stop, observations still
project accounting but do not enqueue redundant workflow signals. Keep the transactional
outbox because PostgreSQL admission and a Temporal RPC cannot share a transaction.
Keep agent resource ownership and sensitive request loading inside activities.

Use typed Temporal service calls for namespace registration, accepting only
AlreadyExists and verifying the resulting namespace state and retention. Remove
shell output parsing, jq and its custom image. Reuse the upstream schema tool.
Retain private transport, distinct credentials and deployment boundaries.

Validate concurrency, rollback, lost acknowledgements, retention, cancellation,
replay, packaging and bootstrap errors with the existing real integration suites.
Run root Python lint, format, strict type checks and pytest. Native Linux/gVisor
checks remain a separate CI gate where local hardware cannot run them.
