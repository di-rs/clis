# Temporal for Boxie

Temporal coordinates Boxie's provisioning, cleanup, and recovery jobs. It runs
as a separate Compose project so an application release does not restart the
workflow server or upgrade its database schemas.

## Set up the service

First provision [PostgreSQL](../postgres/README.md). Then follow the
[operations guide](operations.md) to prepare certificates, initialize the two
Temporal schemas, start the server and UI, and register the `boxie` namespace.
The guide includes commands for fresh installations, upgrades, and verification.
Pinned images are recorded in [versions.json](versions.json).

## Understand the access boundary

Connections use mutual TLS with distinct server, internode, and client identities.
Only Boxie's API, supervisor, the UI backend, and operator tools receive client
certificates. The playground and sandboxes do not join this network.

Every admitted client is trusted as a cluster administrator: the configuration
uses Temporal's no-op authorization. The `boxie` namespace is an organizational
boundary, not a tenant permission boundary. Protect browser access through the
[operator ingress](../ingress/README.md), and keep the UI origin private.

Keep CA signing keys out of runtime containers. Private keys must be owned by
their runtime reader and use mode `0600`; the server/admin images use UID 1000.

## Upgrade and check readiness

Schema upgrades are explicit operator jobs, never API startup work. Bootstrap
verifies the existing namespace and retention; it does not silently change them.
Server health alone does not prove a worker can execute a workflow.

The [runtime checks](operations.md#runtime-verification) test TLS rejection,
database permissions, namespace bootstrap, and workflow execution. Full sandbox
acceptance additionally requires native Linux amd64 with gVisor.
