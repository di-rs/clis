# Boxie

Boxie runs uploaded files in short-lived Linux sandboxes. Services connect through
its private API; the browser playground is one consumer.
Boxie can also run independently.

## Use the API

An integration uploads a ZIP, creates a sandbox from that artifact, connects a
terminal, then stops the sandbox when finished. Start with the
[consumer API guide](docs/consumer-api.md) for the request sequence and terminal
protocol. You need a private API URL and consumer credential from the operator.

The default sandbox provides Bash and sample files. It has no network and limits
CPU, memory, disk, and lifetime. Consumers select uploaded files; they cannot
choose arbitrary commands, environment settings, or isolation profiles.

## Run a deployment

Start with the [infrastructure guide](infra/README.md). You need PostgreSQL,
Temporal, private credentials, and a native Linux host with gVisor (`runsc`).
Docker Desktop can build images, but it cannot validate the required isolation.

The [configuration guide](docs/configuration.md) contains supervisor and API
configuration examples and startup commands. Run one supervisor per data directory,
and make artifact storage visible at the same absolute path to the supervisor
and Docker.

## How it works

Boxie has two processes. The API authenticates consumers and records requests in
PostgreSQL. The supervisor runs containers, stores artifact files, and handles
terminal connections. Only the supervisor has Docker access.

Temporal workers inside the supervisor carry out provisioning, cleanup, and
recovery. PostgreSQL records ownership and capacity so retries do not create a
second sandbox or free a slot before deletion is confirmed.

For resource limits and the mechanisms behind these guarantees, read
[execution and recovery](docs/execution.md). For logs and metrics, see
[monitoring](infra/observability/README.md).

## Develop Boxie

Run focused tests with `uv run pytest packages/boxie/tests`.
Integration tests require their external dependencies; native isolation checks
run through the [lifecycle workflow](../../.github/workflows/boxie-lifecycle.yml).
Passing local tests does not establish production readiness.

[All projects](../../README.md)
