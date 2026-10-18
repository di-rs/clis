# Native workspace commands and directory moves

Moving Boxie to the repository root or moving Rust CLIs between parent
directories should preserve their package names and commands. Cargo and uv
will own package locations. Existing checks, previews, images, and deployment
workflows will consume those locations through native commands.

This is a proposed design for the native workspace refactor and subsequent
CI caching work.

## Scope and constraints

Keep Cargo and uv, the root Python lockfile, and the existing frontend package
manager. Add no Moon, Nx, generic repository command, project registry, generated
workspace configuration, or general task runner. Packaging an existing script
as an ordinary Python command is within scope.

First make the existing layout movable. Actual directory moves and the final
checkout name remain separate choices. Use temporary alternate layouts to test
the result. Preserve the Rust rule that a CLI's package, binary, and leaf
directory have the same name.

Implement native CI caching as a second, independently reviewable phase.
Cache dependencies and Rust compilation inputs/artifacts; continue executing
checks, benchmarks, releases, and service lifecycle tests. Keep existing draft
PR rules, Linux/macOS checks, release approval conditions, artifact validation,
and the PR-only benchmark policy.

## Package locations and commands

The root `Cargo.toml` and `pyproject.toml` remain the authoritative membership
declarations for their ecosystems. Cross-package Rust dependencies inherit
their paths from `[workspace.dependencies]`; start with `cli-log`, whose path
is currently repeated in 20 manifests.

Use `cargo ... --package NAME` for Rust operations and `uv ... --package NAME`
for Python package operations. When another program needs a directory, use
`uv workspace dir --package NAME`. Enable only uv's `workspace-dir` preview
feature and use executable uv 0.13.1 in CI and Docker. Keep Python 3.15, the Rust toolchain, and
existing build-backend versions.

Package the three existing benchmark scripts under `cli_bench`, with
`cli-bench`, `cli-bench-select`, and `cli-bench-report` entry points. Their
arguments and output formats remain compatible. Add `--root PATH` where an
operation needs a checkout; otherwise discover it from the invocation's
workspace. Keep the existing preview script checkout-owned, invoke it relative
to the directory uv resolves, and declare its imported packages in the root
tooling project's dependencies. Service distributions stay independently
installable.

The independent consumer workspace under `tests/consumers` intentionally has
its own dependency paths. Preserve this external-consumer check and document
its manifest as a remaining move boundary; do not fold it into the main
workspace merely to inherit dependencies.

## Existing selectors and policy checks

Benchmark and preview selection will query
`cargo metadata --locked --format-version 1 --no-deps`. Package names,
manifest locations, targets, and local dependency declarations come from Cargo.
The existing selectors retain their own selection behavior and output formats;
this does not create a reusable project-management API.

Determine local dependents from metadata's path dependencies, including build
dependencies, optional dependencies, and target-specific dependencies
conservatively. Metadata without dependencies has no resolved graph; do not
assume its `resolve` field contains one. Benchmark generation also has an
explicit non-Cargo relationship to the package named `biggie`.

Changes to root manifests, lockfiles, or compiler configuration keep their
current conservative selection. Resolve a file's owning package before applying
package-relative source/test/documentation rules. A parent directory named
`docs` or `tests` must not accidentally hide a real CLI's source changes.
Deleted and renamed paths must not become silent skips; root membership changes
select conservatively, and an unresolvable relevant production path selects all
remaining eligible packages.

Keep `cli-check`'s static manifest reader for structural policy diagnostics.
It intentionally checks malformed packages without invoking Cargo. Native
metadata is for executable build/benchmark selection, not a replacement for
those diagnostics.

## Python checks and test assets

Replace root lists of tool directories with repository-root discovery for
pytest and mypy. Use `testpaths = ["."]`, `files = ["."]`, and a single
repository-root Python test import base where necessary. Preserve strict mypy,
test markers, shared dependency fixtures, and explicit exclusions for generated
files and the existing duplicate Pulumi entry point.

Update imports of benchmark modules to their installed package names and
qualify the shared host test mocks through the repository namespace. Remove
script `sys.path` additions that exist only to reach another repository
directory. Preserve the existing pytest import mode unless an observed
collection failure requires changing it.

Use package-local paths for package-owned assets. Use native lookups for
cross-project assets and workspace outputs. Remove fixed ancestor counts that
cross a package boundary. Local implementation paths such as Boxie's migration
directory can remain relative to the owning package.

## GitHub, Docker, and deployment boundaries

GitHub event path filters cannot query checked-out workspace metadata. Replace
project-directory event filters with broad triggers and a cheap selection step
inside the existing workflows. Express component input suffixes relative to
the package directory resolved by uv. Preserve workflow identities, trust
boundaries, default-branch source selection, and complete-run retry guards.
Manual runs still select their requested work. Heavy jobs depend on selection
outputs so unrelated changes only incur the selector job.

Resolve package locations after checkout and uv setup. Use step outputs in
step working directories and action inputs; avoid trying to put runtime step
outputs into workflow-level defaults. Dependabot can use its native recursive
`directories` support for npm manifests, with the owning frontend still using
its existing lockfile.

Python image builder stages copy the filtered repository context and install
only their selected package with uv. This removes the manually enumerated
workspace-manifest copies. Final images still contain only the installed service
environment. Consolidate the service-specific Docker ignore files into one
root policy before broadening the builder copy.

Use a BuildKit named `playground` context for sandbox fixtures and its shell entry
point. Provide it from the directory uv resolves. Frontend builds use the filtered
default workspace context and resolve their package directory inside the builder,
so root Docker exclusions apply to local dependencies and dotenv files. Copy built frontend assets into the
installed package's source location resolved inside the builder, without
encoding its checkout location. Compose receives explicit workspace/project
context paths from its caller; production image-only Compose units keep their
existing host paths.

Setup archives have a stable deployment layout: `boxie/` and `playground/`
contain their respective infrastructure files, and `environment/` contains the
single-host setup files. Producers resolve source directories through uv;
consumers operate on this archive layout. Boxie's installation renderer accepts
its own infrastructure directory, rather than a repository root. Existing
runtime paths under `/opt/clis`, `/etc/clis`, and `/srv/clis` remain operator
configuration, independent of the checkout layout.

## Phase two: native CI caches

Enable setup-uv's dependency cache and setup-vp's dependency cache in ordinary
validation/build jobs. Use the root `uv.lock` plus project manifests for uv
invalidation and the resolved frontend lockfile for setup-vp. Keep caches
disabled in privileged publishers, deployments, and `workflow_run` consumers.

Use `Swatinem/rust-cache` for compilation jobs with its compiler, manifest,
lockfile, environment, and platform keying. Separate debug/check and release
job families. Keep workspace-crate caching off initially so each revision's
candidate is built and benchmarked. Share caches only within compatible jobs.
All new action references use reviewed commit pins.

Record a cold run and a comparable warm run: dependency setup duration, Rust
build duration, total job duration, and cache hit/miss. These are operational
observations, not statistically significant CLI performance measurements.

## Acceptance

A temporary checkout whose outer directory has a different name can move
Boxie to `boxie/`, a CLI to `commands/text/echor/`, the shared logger to
`shared/logging/cli-log/`, and the benchmark tool to `dev/bench/cli-bench/`. After changing
native workspace declarations, refreshing affected lockfiles, and updating
documented external-consumer/README links, commands and selection still work.
Root Python checks collect/type-check moved code, Docker builds use the new
locations, and setup archive contents retain their deployment names.

Service isolation and native lifecycle coverage remain required. Run the
relocated image/lifecycle checks on the Linux job with their existing
prerequisites. A cache miss or disabled cache must still produce passing
checks, and a source or toolchain change must not be hidden by cache reuse.

## References

- [Cargo workspaces and dependency inheritance](https://doc.rust-lang.org/cargo/reference/workspaces.html)
- [Cargo metadata](https://doc.rust-lang.org/cargo/commands/cargo-metadata.html)
- [uv workspace directory preview](https://docs.astral.sh/uv/concepts/preview/#workspace-dir)
- [Docker named contexts](https://docs.docker.com/build/concepts/context/#named-contexts)
- [Testing](../../testing.md) and [Benchmarking](../../benchmarking.md)
