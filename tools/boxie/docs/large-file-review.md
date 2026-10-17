# Large-file review for PR 39

Reviewed tracked source, test, workflow and shell files with at least 300 lines
on the PR branch before this cleanup. Counts include comments and blank lines;
they are a prompt to inspect responsibilities, not a mandatory size limit.
Generated lockfiles, test fixtures and prose are not implementation modules.

## Changes in this cleanup

| File (original lines) | Decision and reason |
| --- | --- |
| `tools/boxie/src/boxie/sandbox/registry.py` (457) | Split into `registry/records.py`, `registry/resources.py` and `registry/orchestrator.py`, with explicit package exports. Records define persistence; resource functions handle Docker provisioning/deletion; the orchestrator retains admission, task ownership and deadline coordination. Keep the coordinator together even though it remains just over 300 lines: admission, stopping and expiry share mutable state and event-loop ordering. |
| `tools/boxie/src/boxie/sandbox/client.py` (300) | Extract WebSocket connection/terminal handling to `client_terminal.py`; keep authenticated HTTP operations in the client. |
| `tools/boxie/tests/test_agent.py` (550) | Rename and split into orchestrator admission, cleanup, terminal and Docker-adapter suites. Preserve the existing assertions and shared transport support. |
| `tools/boxie/tests/test_artifacts.py` (358) | Extract ZIP metadata/parser compatibility tests to `test_zip_metadata.py`; retain artifact publication, safety and retirement tests. |
| `tools/cli-playground/src/playground/routes.py` (387) | Extract host-boundary middleware and terminal relay to `transport.py`; keep endpoint wiring and HTTP models in routes. |

## Other files changed by PR 39

| File (lines before cleanup) | Decision and reason |
| --- | --- |
| `tools/cli-playground/src/playground/service.py` (421) | Keep in this change. Upload publication, admission recovery and PR closure coordinate through the same persisted intent store and upload lock. A future split should introduce explicit publication/session services and retain the cross-operation race tests, rather than move methods into mutually dependent files. |
| `tools/cli-playground/infra/playground_infra/deploy.py` (309) | Keep for now. Configuration, release validation and deployment form one small operator entry point, and deployment is deliberately disabled during the staged extraction. Revisit the release-validation boundary when production activation is implemented. |
| `tools/cli-playground/frontend/e2e/playground.spec.ts` (514) | Keep. End-to-end scenarios share browser setup and cover the same preview/terminal journey. Split by build-state and terminal interactions if those suites grow independently. |
| `tools/cli-playground/frontend/src/terminal/session.test.ts` (358) | Keep. Cohesive state-machine tests with shared fake socket/terminal setup; production session logic is already separate. |
| `tools/cli-playground/tests/test_api.py` (479) | Keep. Consumer-facing API scenarios validate publication and authorization together. A build/publication versus host-boundary split is a reasonable follow-up, but is not needed for this internal rename. |
| `tools/cli-playground/tests/test_service.py` (429) | Keep beside the service for now. These race/recovery scenarios are important evidence for any later service decomposition. |

## Files outside PR 39's implementation scope

These were inspected for responsibilities but are not changed by this cleanup.

| File (lines) | Assessment |
| --- | --- |
| `biggie/src/text.rs` (320) | Keep: text generation options, validation and streaming implementation form one cohesive module; API documentation contributes to the count. |
| `biggie/tests/cli.rs` (1458) | Worth a separate refactor into package-owned generation-mode suites with shared helpers. Much broader than the Boxie change. |
| `biggie/tests/library.rs` (746) | Worth grouping by generation mode and writer/error contracts alongside the CLI-test cleanup. |
| `coreutils/commr/tests/cli.rs` (307) | Keep: one command's compatibility cases and fixtures. |
| `coreutils/cutr/tests/cli.rs` (357) | Keep: one command's field/character selection contract. |
| `coreutils/findr/tests/cli.rs` (344) | Keep: one command's traversal/filter contract. |
| `coreutils/grepr/tests/cli.rs` (444) | Keep: one command's matching and output-mode matrix. |
| `coreutils/headr/tests/cli.rs` (428) | Keep: one command's byte/line and multi-file compatibility cases. |
| `coreutils/lsr/tests/cli.rs` (432) | Keep: one command's listing/metadata compatibility cases. |
| `coreutils/tailr/tests/cli.rs` (933) | Worth a separate split into line/byte offsets and input/error behavior, preserving package fixtures and executable-based assertions. |
| `coreutils/uniqr/tests/cli.rs` (649) | Consider grouping comparison options and output modes when extending coverage; current fixture-driven suite remains understandable. |
| `kara/src/editor.rs` (421) | Candidate for extracting command dispatch and builder setup; keep terminal lifecycle ownership in the editor. Separate editor work. |
| `parsu/src/lib.rs` (301) | Keep: small grammar implementation with colocated examples/tests; combinators are already separate. |
| `tools/cli-bench/bench.py` (474) | Candidate for suite parsing/validation versus execution/report validation modules in a benchmark-tool change. |
| `tools/cli-bench/report.py` (449) | Candidate for result parsing/rendering versus GitHub publication modules. Do alongside benchmark-tool work. |
| `tools/cli-bench/tests/conftest.py` (423) | Candidate for explicit fake-tool support modules; retain pytest fixture registration in conftest. |
| `tools/cli-playground/preview_ci.py` (616) | Candidate for selection, publication and PR lifecycle modules, preserving its security and stale-run checks. Separate from sandbox internals. |
| `tools/cli-playground/tests/test_preview_ci.py` (884) | Split by those same responsibilities if preview CI is refactored; keep its security regression coverage intact. |
| `utils/cli-log/tests/cli.rs` (383) | Keep: one public logging contract exercised through consumer binaries. |

`frontend/pnpm-lock.yaml` is generated dependency resolution data: never split it
for line count. The Boxie design document is a cross-stage specification, so its
length also does not justify code-style decomposition. Other documentation and
fixture payloads are similarly outside this source-module review.
