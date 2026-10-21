# Boxie consumer API

Use this private HTTP and WebSocket API to upload files, create a sandbox, and
connect a terminal. You need the API URL and a consumer secret from your operator.
Keep the secret on your server; do not send it to a browser.

- [Manage artifacts and sandboxes](#manage-artifacts-and-sandboxes)
- [Connect a terminal](#connect-a-terminal)
- [Check service health](#check-service-health)
- [Follow a request through logs](#follow-a-request-through-logs)

## Manage artifacts and sandboxes

Every resource request and terminal handshake requires a consumer Bearer secret.
Resources owned by another consumer return 404. Treat resource UUIDs as opaque
identifiers.

| Operation | Request and response |
| --- | --- |
| Upload artifact | `PUT /v1/artifacts/{uuid}`, raw ZIP, `X-Content-SHA256`; 201 new, 200 exact retry |
| Retire artifact | `DELETE /v1/artifacts/{uuid}`; 204 |
| Create sandbox | `POST /v1/sandboxes`, `Idempotency-Key`, `{"artifact_id":"UUID"}`; 202 with status |
| Inspect sandbox | `GET /v1/sandboxes/{uuid}`; 200 with status |
| Stop sandbox | `DELETE /v1/sandboxes/{uuid}`; 200 with status |
| Terminal | `WS /v1/sandboxes/{uuid}/terminal` |

### Request sequence

1. Upload a ZIP under a new artifact UUID. Set `X-Content-SHA256` to the digest
   of the ZIP bytes. An exact retry returns 200 instead of 201; reusing the UUID
   with a different digest returns 409.
2. Create a sandbox with `{"artifact_id":"UUID"}` and an `Idempotency-Key`.
   The 202 response means the request was accepted; inspect its state before
   connecting a terminal.
3. Use the returned sandbox `id` to inspect it, attach a terminal, or stop it.
4. Retire the artifact when it is no longer needed for new sandboxes. Boxie
   keeps its files until existing sandbox references have been released.

Sandbox status includes `id`, `state`, `created_at`, `absolute_deadline`,
`reason`, and `cleanup_pending`. A stop can leave cleanup pending: capacity is
released only after container deletion is confirmed.

### Creation and retries

Sandbox POST accepts only `artifact_id`. Boxie resolves its default shell, TERM,
limits and lifetime settings before admission and retains the accepted snapshot
for retries and recovery. Profile, command, environment, policy, and other unknown
fields are rejected.

Reuse an idempotency key only for the same request; changing the request conflicts
with the previously accepted specification.

| Status | Meaning |
| --- | --- |
| 422 | Invalid request body |
| 409 | Retry conflicts with an accepted request |
| 429 | Sandbox capacity is exhausted |
| 503 | The supervisor is unavailable |

An API restart preserves resource IDs, idempotency records, and capacity accounting.
The supervisor continues draining terminal output independently.

## Connect a terminal

### Attach and send input

Open the terminal WebSocket and send an `attach` JSON text frame:

```json
{"type":"attach","attachment_order":1,"resume_after":0,"cols":80,"rows":24}
```

A successful attachment returns an epoch for that connection:

```json
{"type":"attached","epoch":1}
```

Use the returned epoch in subsequent controls. For example, send user input with:

```json
{"type":"input","epoch":1,"source":"user","data":"echo hello\n"}
```

Terminal-generated replies use `source: terminal`; they do not renew idle time.

### Order attachment attempts

An attachment order identifies a connection attempt; an epoch identifies the
accepted connection and prevents an older connection from controlling it.

Allocate a strictly increasing `attachment_order` for each sandbox execution,
from 1 to 9007199254740991. Every new attempt needs a higher order, including a
retry whose previous result is uncertain.

The supervisor saves the highest accepted order before acknowledging attachment.
Equal or older orders are rejected without changing the epoch or disconnect grace,
even if the old request was already travelling through a relay. Repeating an
accepted order does not replay its success.

A rejected cursor does not consume the order. If the journal cannot save an
attachment, the supervisor reports no successful attachment and disables execution
readiness until the pending state is persisted.

### Control and output frames

ACK, resize, heartbeat, and detach controls carry the current epoch.

| Frame or buffer | Format or limit |
| --- | --- |
| JSON control | At most 16 KiB of UTF-8, including JSON |
| Terminal size | 1-500 columns and 1-300 rows |
| Binary output | Eight-byte unsigned big-endian sequence, then at most 16 KiB of raw terminal (PTY) bytes |
| Retained output | At most 1 MiB of payload and 4096 frames |
| Status | JSON with `type: status` and a termination `reason` |

Output sequences start at one and continue across attachments. Acknowledge bytes
only after applying them; reconnect with the last applied sequence. A cursor older
than the retained output returns `stream_gap`; a cursor ahead of the output is invalid.

The supervisor keeps draining output even when no reader is connected. Replayed
bytes and control metadata do not count toward the cumulative 16 MiB PTY output limit.

### Replay and resize

Geometry controls identify the output sequence before a size change. Apply output
through that sequence before changing dimensions. Repeating the same dimensions
is safe.

After receiving `replay_complete`:

1. Drain pending output writes.
2. Request the terminal size.
3. Wait for `resized` before enabling user input.

Terminal-generated replies remain allowed during replay. Boxie retains dimensions
for each output frame and one current tail value, even if a resize acknowledgment
is lost. This orders frames and resize acknowledgments; it cannot establish the
exact kernel timing of bytes already queued in Docker.

### Heartbeats and disconnects

Send browser-origin heartbeat controls every ten seconds. After thirty seconds
without one, the supervisor disconnects the path. Relay-local WebSocket pings
do not renew that deadline.

A detected disconnect retains the terminal for its two-minute grace, subject to
the earlier absolute or idle deadline. A successful replacement
attachment receives a new epoch; stale controls cannot affect it.

The API provides output replay, not a saved screen snapshot. The
[playground](../../../tools/cli-playground/README.md#terminal-recovery) describes
how its browser client uses this protocol.

## Check service health

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

### Correlate retries and later actions

Creation and a later stop are separate actions. Boxie saves their first accepted
origins with the sandbox and outbox, preserving them across retries and worker
restarts. Admission retries keep their own response IDs while the saved creation
origin stays unchanged. Use `sandbox_id` to join the resource's whole lifetime.
Periodic activity decisions have their own retry-stable IDs. Historical records
and unavailable telemetry lookups can omit the originating ID; resource and
workflow identifiers remain available.

Boxie requires no consumer package or shared logging runtime. See the
[operator logging guide](observability.md) for collection and searches.
