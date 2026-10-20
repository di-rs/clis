# Component naming and Boxie independence

The approved implementation gives each deployed responsibility one name and
makes cli-playground a consumer of an independently usable Boxie service.

| Component | Responsibility | Image or identifier |
| --- | --- | --- |
| Sanctuary | Shared repository and installation composition | `sanctuary`, `SANCTUARY_*` |
| cli-playground Frontend | Browser UI and terminal interaction | `frontend` within the application |
| cli-playground Backend | Preview management and the Boxie HTTP/WebSocket client | `backend` within the application |
| cli-playground | Both application components in one deployed image | `cli-playground`, `CLI_PLAYGROUND_IMAGE` |
| Boxie API | Consumer authentication and durable admission | `boxie-api` |
| Boxie Supervisor | Execution, terminals, cleanup and embedded Temporal workers | `boxie-supervisor` |
| Boxie | Service image used by API and Supervisor | `boxie`, `BOXIE_IMAGE` |
| Boxie Sandbox | Isolated execution with the fixed default environment | `boxie-sandbox`, `BOXIE_SANDBOX_IMAGE` |

Boxie owns one Ubuntu 24.04 Linux x86_64 environment, Bash startup, standard
utilities and built-in fixtures. Consumers upload the existing bounded flat ZIP
format and create a sandbox with an artifact reference. Authentication,
idempotency, isolation, resource bounds and retained accepted snapshots remain.
Consumer profile, command, environment and policy fields are rejected. No new
bundle layout or configurable template framework belongs to this increment.

Boxie builds without cli-playground manifests or source files. Its installation
accepts a generic consumer identity from Sanctuary composition. cli-playground
owns its preview token and application settings; its release does not modify
Boxie's profiles or restart the Supervisor. Standalone Boxie lifecycle tests use
a generic API consumer. Application/browser tests compose Boxie separately.

Future template selection and consumer-provided templates require a separate
plan, recorded in [issue #70](https://github.com/di-rs/sanctuary/issues/70).
The Temporal/API and PostgreSQL/application-service investigations remain separate.
The fixed default does not determine the future template contract.

The old installation is disposable. Follow the
[reset procedure](../../component-naming-cutover.md), then remove the obsolete
one-time converters and cutover documentation in the separate cleanup PR.
Keep the Alembic revision chain and historical execution serializers required
for database initialization and replay. No live reset is authorized by the code PR.
