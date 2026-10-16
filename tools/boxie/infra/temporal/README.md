# Boxie Temporal deployment

Boxie owns this deployment package. It runs the production Temporal server
as a separate Compose project with explicit schema operations, so ordinary
Boxie application releases do not restart the server or upgrade its schemas.
Production never uses a development server.
[versions.json](versions.json) records the verified upstream releases and exact
multi-platform image identities: server/admin-tools 1.32.0 and UI 2.56.0.
Schema jobs use the unchanged upstream admin-tools image and its schema tool.
Namespace bootstrap reuses the approved immutable `BOXIE_RUNTIME_IMAGE` with
Boxie's pinned Temporal Python SDK and requires no separate helper image. The
bootstrap job explicitly retains the prior operator UID/GID `1000:1000`.

## Trust and private transport

The Temporal package owns certificate issuance and rotation. Use separate CAs
for frontend server certificates, internode identities, and admitted client
identities. Keep CA signing keys outside all runtime mounts. Server certificates
need DNS SAN `temporal`; internode certificates need DNS SAN
`temporal-internode` and both ServerAuth and ClientAuth EKUs. The frontend trusts
only the dedicated client CA plus the internode CA for its required internal
clients. Internode mTLS uses only the internode CA. Server config disables the
unused frontend HTTP API and never disables hostname verification.

Issue distinct client identities only to Boxie API, Boxie workers, the UI
backend, and trusted operator tools. Playground and sandbox containers receive
neither certificates nor control-network membership. Mount only each process's
own keys and public trust roots; the server's internal worker reuses its
internode identity. Private keys must be readable only by the relevant runtime
UID (the server/admin images use UID 1000) and their operator. Certificates are
operator inputs, not committed files.

Empty authorizer and claim mapper select the upstream no-op authorization
implementations. **Every admitted mTLS client is a fully trusted Temporal cluster
administrator.** Namespace `boxie` is organizational, not an authorization
boundary; no per-user or namespace RBAC is claimed. Cloudflare Access must
protect the UI's eventual ingress in PR3; the UI origin here stays internal.
Temporal sees the UI backend identity, not the browser user's identity.

Provide these read-only certificate directories:

| Variable | Required files |
| --- | --- |
| `TEMPORAL_CERTS_DIR` | `frontend.pem`, `frontend.key`, `frontend-ca.pem`, `internode.pem`, `internode.key`, `internode-ca.pem`, `client-ca.pem` |
| `TEMPORAL_OPERATOR_CERTS_DIR` | `operator.pem`, `operator.key`, `frontend-ca.pem` |
| `TEMPORAL_UI_CERTS_DIR` | `ui-client.pem`, `ui-client.key`, `frontend-ca.pem` |

Set the existing platform's `BOXIE_CONTROL_NETWORK`, `TEMPORAL_DB_PASSWORD`,
`VISIBILITY_DB_PASSWORD`, and approved immutable `BOXIE_RUNTIME_IMAGE`.
The two database credentials must be distinct. The
server binds to its container IP for membership, listens privately, and starts
frontend/history/matching/worker roles explicitly. It receives no Boxie database
credential or Docker socket. UI backend TLS explicitly verifies the server name.

## Explicit operations

Start the [platform](../../../../infra/platform/README.md) first. From this directory:

```sh
# Empty pre-created databases only:
docker compose run --rm schema setup
docker compose up -d temporal ui
docker compose run --rm bootstrap
```

On a reviewed server version upgrade, update the committed image identities,
then run `docker compose run --rm schema update` explicitly before starting that
server version. Schema errors fail the operation; there are no overwrite/drop
flags and no schema jobs tied to API startup. Persistence and visibility schemas
are upgraded using their separate least-privilege database credentials.

Bootstrap calls the SDK's typed namespace describe/register service methods over
mTLS. Only the `NOT_FOUND` RPC status causes registration; only `ALREADY_EXISTS`
is accepted as a concurrent registration race. A fresh describe after registration
verifies the namespace name, registered state and seven-day retention.
Transport and authentication failures propagate without becoming namespace absence.
Running bootstrap repeatedly is safe and verifies existing retention rather than
silently changing it.
After successful verification, the operator command waits 20 seconds for namespace
cache propagation, including when a previous bootstrap was interrupted. Temporal
[documents registration taking up to 15 seconds](https://docs.temporal.io/self-hosted-guide/namespaces).
The grace does not substitute for the workflow readiness check below.

Frontend health alone does not prove workflow readiness. Check the Boxie
namespace and workflow/activity execution before opening new admission. The SQL
adapter closes new admission when dependencies fail, the oldest undelivered
command exceeds 30 seconds, or backlog reaches 100. Matching retries, status,
and durable stop remain available while PostgreSQL is reachable. Workers must
load sensitive spec/snapshot data inside activities; never serialize it into
workflow history.

## Runtime verification

From the repository root:

```sh
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked pytest -m integration tools/boxie/tests/test_temporal_deployment.py
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked python tools/boxie/tests/run_integration.py
```

The integration harness requires Docker/Compose support and OpenSSL. It invokes
the namespace command with the installed pinned SDK and operator certificate;
production invokes the same module from the approved Boxie runtime image. It
creates isolated projects, fresh databases, two-day test certificates, and
loopback-only port overrides. Its test bridge permits those host clients; the
production network remains internal and publishes no ports. Test keys are
readable by the disposable container UIDs; this test permission choice is not a
production key policy. No CA private key is mounted into a runtime container.
Only the harness's projects/volumes are removed, including after failure.

The gate runs both schema operations, bootstrap twice, valid API/worker/operator
and UI identities, no-certificate/foreign-CA/wrong-hostname rejection, a real
workflow/activity, the actual UI namespace API, and six cross-database credential
denials. It writes nonsecret runtime evidence into pytest's temporary directory.
Linux arm64/runc proves this infrastructure path, not the later native
Linux/amd64/runsc full-stack gate or production deployment.

The [durable lifecycle workflow](../../../../.github/workflows/boxie-lifecycle.yml)
adds interpreter imports, pinned test-server history replay/cancellation and the
native Linux amd64/runsc full stack. Its test-only agent launcher observes
collector entry/completion around a successful SQL COMMIT with lost acknowledgment;
collection logic, cadence and deadlines remain production code. Owned Docker/SQL
proxies and read-only journal mounts exercise process loss and truthful accounting.

## Primary sources

- [Server 1.32.0 release](https://github.com/temporalio/temporal/releases/tag/v1.32.0)
- [UI 2.56.0 release](https://github.com/temporalio/ui/releases/tag/v2.56.0)
- [Server config template processing](https://github.com/temporalio/temporal/blob/v1.32.0/common/config/loader.go)
- [TLS configuration](https://docs.temporal.io/references/service-configuration#tls)
- [mTLS deployment guidance](https://docs.temporal.io/self-hosted-guide/security#client-connections)
- [Pinned authorizer selection](https://github.com/temporalio/temporal/blob/v1.32.0/common/authorization/authorizer.go)
- [Pinned SDK cancellation and activity usage](https://github.com/temporalio/sdk-python/blob/1.34.0/README.md)
- [Typed workflow service API](https://python.temporal.io/temporalio.service.WorkflowService.html)
