# cli-check

`cli-check` checks the workspace's CLI package layout, tests, documentation, and
benchmark declarations. It reads repository files; it does not execute the CLIs
or prove that their behavior is correct.

## Check a package

Run from the repository root after `uv sync --all-packages`:

```sh
uv run cli-check -p echor
```

Repeat `-p` for more packages, or omit it to inspect the workspace. Success prints
`CLI checks passed`. Failures go to stderr with a path and rule identifier.
Exit codes are 0 for success, 1 for violations or invalid repository data, and 2
for invalid arguments.

## Select a check

| Command after `uv run` | What it checks |
| --- | --- |
| `cli-check layout -p echor` | One matching package, directory, and binary, using `src/main.rs` |
| `cli-check tests -p echor` | CLI integration test registration, helper dependency, and fixture layout |
| `cli-check docs -p echor` | Package README, root index entry, and local link targets |
| `cli-check benches -p echor` | Benchmark suite or explicit exemption |

| Argument or option | Meaning |
| --- | --- |
| `layout`, `tests`, `docs`, or `benches` | Optional check group; omit it to run all four. |
| `-p, --package NAME` | Select a Cargo package; repeat for more packages. Default: whole workspace. |
| `--root PATH` | Select another Cargo workspace. |
| `-h, --help` | Print help. |

See the [check reference](docs/checks.md) for inventory limits and benchmark
exemptions. Markdown fragment validation belongs to the separate link checker.

## Develop the checker

```sh
uv run pytest tools/cli-check/tests
```

Tests exercise the real command in temporary workspaces. The normal commit hook
selects affected packages; run `cli-check` directly during development rather
than invoking the hook runner. Python CI checks the full workspace.

## Architecture

[Workspace discovery](src/cli_check/workspace.py) reads Cargo package metadata.
The [command](src/cli_check/command.py) selects packages and dispatches to independent
layout, test, documentation, and benchmark checks. Checks return diagnostics rather
than modifying packages. [Affected-package selection](src/cli_check/affected.py)
and [hook planning](src/cli_check/hooks.py) keep repository integration separate
from the rules themselves.
