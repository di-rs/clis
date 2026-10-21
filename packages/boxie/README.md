# Boxie

Boxie is Sanctuary's sandbox runtime. It runs uploaded files in short-lived Linux
sandboxes through a private API; the browser playground is one consumer.
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

Start with the [infrastructure guide](docs/deployment.md). You need PostgreSQL,
Temporal, private credentials, and a native Linux host with gVisor (`runsc`).
Docker Desktop can build images, but it cannot validate the required isolation.

The [configuration guide](docs/configuration.md) contains supervisor and API
configuration examples and startup commands. Run one supervisor per data directory,
and make artifact storage visible at the same absolute path to the supervisor
and Docker.

## How it works

The API records requests; the supervisor owns physical execution. Read
[architecture](docs/architecture.md) for the component boundaries and
[execution and recovery](docs/execution.md) for lifecycle guarantees.

## Develop Boxie

Run focused tests with `uv run pytest packages/boxie/tests`.
Integration tests require their external dependencies; native isolation checks
run through the [lifecycle workflow](../../.github/workflows/boxie-lifecycle.yml).
Passing local tests does not establish production readiness.

## Documentation

- [Architecture](docs/architecture.md)
- [Consumer API](docs/consumer-api.md)
- [Configuration](docs/configuration.md)
- [Execution](docs/execution.md)
- [Database](docs/database.md)
- [Observability](docs/observability.md)
- [Deployment](docs/deployment.md)
- [Temporal](docs/temporal.md)

[All projects](../../README.md)
