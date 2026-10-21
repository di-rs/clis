# Boxie consumer API

## Consumer API

Every resource request and terminal handshake requires a consumer Bearer secret.
Foreign resources return 404. UUIDs and digests remain opaque to the API.

| Operation | Request and response |
| --- | --- |
| Upload artifact | `PUT /v1/artifacts/{uuid}`, raw ZIP, `X-Content-SHA256`; 201 new, 200 exact retry |
| Retire artifact | `DELETE /v1/artifacts/{uuid}`; 204 |
| Create sandbox | `POST /v1/sandboxes`, `Idempotency-Key`, `{"artifact_id":"UUID"}`; 202 with status |
| Inspect sandbox | `GET /v1/sandboxes/{uuid}`; 200 with status |
| Stop sandbox | `DELETE /v1/sandboxes/{uuid}`; 200 with status |
| Terminal | `WS /v1/sandboxes/{uuid}/terminal` |

Sandbox POST accepts only `artifact_id`. Boxie resolves its default shell, TERM,
limits and lifetime settings before admission and retains the accepted snapshot
for retries and recovery. Profile, command, environment, policy and other unknown
fields are rejected. Invalid bodies return 422, conflicting retries 409, capacity exhaustion
429 and unavailable supervisor calls 503. API restarts retain SQL-owned IDs,
idempotency records and capacity; the supervisor keeps draining terminal output.

Terminal attachments start with a JSON text frame
`{"type":"attach","attachment_order":1,"resume_after":0,"cols":80,"rows":24}`. The response
`{"type":"attached","epoch":1}` fences subsequent controls. Input carries
`{"type":"input","epoch":1,"source":"user","data":"echo hello\n"}`;
parser-generated replies use `source: terminal` and do not renew idle time.
The consumer allocates a strictly increasing `attachment_order` per sandbox
execution (1–9007199254740991). The supervisor persists the highest accepted order with
its attachment state before returning success. Equal/older orders fail before any
epoch or grace change, even when an older request was already in flight through a
relay. A new attempt or retry after an uncertain result must use a higher order;
repeating an accepted order is rejected, not an idempotent attach replay. A failed
cursor validation does not consume the order. Journal failure exposes no successful
attachment and disables execution readiness until the pending state is persisted.
cli-playground supplies this value from its persisted page claim; browser frames cannot
select or override it.
ACK, resize, heartbeat and detach also carry the current epoch. Controls are at
most 16 KiB of UTF-8 including JSON, columns are 1–500 and rows 1–300.

Each binary output frame contains an eight-byte unsigned big-endian sequence
followed by at most 16 KiB of raw PTY bytes. Sequences start at one and continue
across attachments. ACK only after applying bytes; reconnect with the last applied
sequence. The supervisor retains at most 1 MiB of payload and 4096 frames while draining
independently of readers. An evicted cursor returns `stream_gap`; a future cursor
is invalid. Replayed bytes and control metadata do not count toward the 16 MiB
cumulative PTY output limit. JSON status frames contain `type: status` and a
termination `reason`.

Geometry controls identify the preceding sequence. Drain writes to that boundary
before applying dimensions; repeated dimensions are idempotent. After
`replay_complete`, drain writes and request resize, then wait for `resized` before
enabling user input. Parser replies remain allowed during replay. Geometry is
retained per output frame plus one current tail value, including when a resize
acknowledgment is lost. This defines observable frame-assignment/resize-success
order, not exact kernel-time causality for bytes already queued in Docker.

Send browser-origin heartbeat controls every ten seconds; the supervisor disconnects
a path after thirty seconds without one. Relay-local WebSocket pings do not renew
that deadline. A detected disconnect retains the terminal for its two-minute
grace, subject to the earlier absolute or idle deadline. A successful replacement
attachment receives a new epoch; stale controls cannot affect it. Credentials and
cursors stay in page memory. Page reload starts fresh; restart continuity and
screen snapshots are not supported.

`GET /healthz` is unauthenticated liveness. Authenticated `GET /readyz` returns
the API release and SQL, supervisor and Temporal readiness, with 503 when unavailable.
The supervisor's authenticated `/readyz` reports Docker, SQL and execution persistence. Access logs are disabled by both entry points.

## Follow a request through logs

Send an optional `X-Request-ID` with an authenticated HTTP request or WebSocket
handshake to correlate your own service logs with Boxie. Use one canonical,
lowercase UUID, for example `11111111-1111-4111-8111-111111111111`. Boxie returns
the selected ID in that header on application HTTP responses, including errors,
and WebSocket acceptance or HTTP denial. WebSocket frames stay unchanged.

A missing, malformed, uppercase, duplicate or comma-joined header gets a fresh
UUID. Unauthenticated requests and liveness probes also get fresh IDs, regardless
of a supplied value. IDs are diagnostic metadata and never replace credentials,
ownership checks or idempotency keys. Consumers can omit the header entirely.

Creation and a later stop are separate actions. Boxie saves their first accepted
origins with the sandbox and outbox, preserving them across retries and worker
restarts. Admission retries keep their own response IDs while the saved creation
origin stays unchanged. Use `sandbox_id` to join the resource's whole lifetime.
Periodic activity decisions have their own retry-stable IDs. Historical records
and unavailable telemetry lookups can omit the originating ID; resource and
workflow identifiers remain available.

Apply Alembic revision `0006_request_correlation` before updating both Boxie
processes. Its nullable columns support older writers; keep those columns when
rolling the application back. Boxie requires no consumer package or shared
logging runtime. See the [operator logging guide](../infra/observability/README.md)
for collection and searches.
