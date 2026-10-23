# Sanctuary threat model

Use this model for changes to input handling, authentication, sandboxing,
persistence, CI publication, or infrastructure. Protect host integrity, session
and consumer isolation, credentials, artifact integrity, lifecycle state, and
service availability. User files and terminal contents may contain sensitive data.

## Trust boundaries

- **Local CLIs:** Arguments, stdin, files, and filenames are untrusted. Commands
  run with the invoking user's permissions; parsing must not cause unintended
  reads, writes, or execution.
  Kara's atomic saves must preserve existing owner, group, mode, and access ACLs
  or fail before replacement. Staging files must not expose content through inherited
  access rules. Errors after replacement must report committed data with uncertain
  durability rather than an unchanged destination.
- **Browser to playground:** HTTP requests and terminal frames are untrusted.
  [Routes](../tools/cli-playground/src/cli_playground/routes.py) check origins and
  session bindings. Preserve session authorization across reconnects and revision
  changes. Origin checks do not authenticate arbitrary clients or prevent abuse.
- **Playground to Boxie:** The [private API](../packages/boxie/README.md) derives
  consumer identity from credentials and enforces resource ownership. Keep
  consumer and supervisor credentials separate and out of browser responses.
  Operators own execution profiles; consumers select artifacts.
- **Artifacts to host storage:** ZIPs and executable bytes remain untrusted after
  admission. Preserve path/type validation and expanded-byte limits in
  [Boxie extraction](../packages/boxie/src/boxie/persistence/artifact_files.py)
  and [playground validation](../tools/cli-playground/src/cli_playground/artifacts.py).
  Valid executable headers do not establish trust.
- **Sandbox to host:** Only the supervisor receives Docker access. Preserve
  [runsc isolation](../packages/boxie/src/boxie/sandbox/docker.py), the non-root
  user, disabled network, read-only root and artifacts, dropped capabilities,
  and resource limits, including during recovery.
- **PR build to publication:** PR source and build outputs are untrusted.
  The [privileged controller](../.github/workflows/cli-playground-preview.yml)
  uses default-branch code. Never execute PR-controlled scripts or binaries with
  publication or deployment credentials. Apply the same boundary to benchmark
  reporting and release automation.
- **Local fixture recipes and recordings:** CLI test catalogs and their reference
  commands are trusted repository code, executed with the developer's permissions.
  Temporary test workspaces do not sandbox host access. `cli-testing record`
  stages output outside committed fixtures; `apply` is an explicit promotion of a
  reviewed capture. Validate catalog ownership, allowed paths, input/expectation
  hashes, reference identity, completion, and shared-file conflicts before writes.
  A recording's provenance is consistency metadata, not authentication of an
  arbitrary downloaded artifact.

## Review priorities

Trace attacker-controlled input to the affected asset; state required credentials,
reachability, impact, and the control bypassed. Prioritize cross-session or
cross-consumer access, sandbox escape, archive traversal, resource exhaustion,
secret disclosure, and privileged execution of PR-controlled code.

Check rejected requests, stale attachments, retries, and recovery. Failures must
not leave unauthorized sessions, published files, or indefinite capacity
reservations. Keep terminal contents and credentials out of logs and metrics.
Distinguish local CLI failures from remotely reachable service vulnerabilities.

## Deployment assumptions

The intended topology exposes only the playground. Confirm that Boxie,
Docker, PostgreSQL, Temporal, and metrics remain private; verify TLS, proxy/Host
handling, authentication, and operator-only writes to profiles and storage.
Confirm native Linux/gVisor enforcement rather than assuming container isolation.

Public admission rate limits, credential rotation, backup access, and retention
need operator confirmation. Capacity limits alone do not establish abuse controls.
An already-compromised operator, host kernel, or Docker daemon is outside this
application-level model; paths for untrusted inputs to gain that access are in scope.

Update this model when boundaries or deployment assumptions change. This file
informs repository reviews; it does not configure Codex Security Cloud monitoring.
Its structure follows [OpenAI's threat-model guidance](https://learn.chatgpt.com/docs/security/threat-model).
