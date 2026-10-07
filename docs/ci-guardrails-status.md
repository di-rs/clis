# Guardrail verification

[Contributor commands](../CONTRIBUTING.md#checks) · [Guardrail scope](ci-guardrails.md)

The branch includes master `0d8caa8`. The Rust tree passes 777 workspace tests,
nine doctests, strict Clippy/rustdoc, Biggie's CSV feature combinations and the
separately locked consumer. The macro diagnostics fix rejects incomplete trailing
clauses that were previously accepted. Dependency audits cover 226 root, 58
consumer and 61 fuzz packages.

[Native CI](https://github.com/di-rs/clis/actions/runs/37499609165) and
[both fuzz jobs](https://github.com/di-rs/clis/actions/runs/37499609222) passed
before the configuration simplification. This revision removes the repository
script layer; equivalent Cargo and security-tool commands are visible in CI and
prek configuration. Zizmor remains required in CI and gains a local hook.
The updated workflow run must pass before treating the simplified wiring as
verified remotely.

On 2026-10-07 the direct commands passed locally: all 777 tests and nine doctests,
strict Clippy, the standalone consumer, both 15-second fuzz runs with unchanged
lockfiles, refreshed audits of all three graphs, cargo-deny/machete, actionlint,
zizmor, local links and spelling. The `zizmor` and `domain-clippy` hooks also pass.

Earlier deliberate probes rejected first-party unsafe code, forbidden licenses
and registries, unavailable advisory data and a broken documentation anchor.
A hanging test was terminated at the configured 120-second limit.
Targeted Miri passed 12 parsu and four tail tests. The earlier optimized and
coverage run passed 742 tests before master's later Biggie additions; that
count is not relabeled as evidence for the newer tree.

Master ruleset `24529961` was read back with ten required Actions checks,
current-base enforcement and no bypass actors. A draft PR alone does not prove
the failed/missing-check merge-state scenario. Dependabot security updates are
enabled; secret scanning and push protection remain enabled.

Maintenance workflow dispatch previously returned HTTP 404 before registration
on the default branch. Its scheduled native runs remain pending merge and actual
execution. Stable/beta readiness probes do not establish an MSRV guarantee.
Existing findr/lsr/pwdr boundary debt and Kara's nightly requirement remain separate
migrations. Release work is excluded.
