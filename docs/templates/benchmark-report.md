# CLI benchmark report template

Follow the [shared procedure](../benchmarking.md#required-workflow-for-cli-changes).
Copy this into the affected app's documentation and adjust links. Replace prompts
with observed evidence, or explicitly mark a metric unmeasured/inapplicable with a
reason. This file is a template, not a recorded result.

## Scope and verdict

- Utility, change, user workload and priority cases: [details].
- Result: [improved/regressed/inconclusive/unmeasured per metric and case].
- Final-source coverage: [exact measured source; edits made after measurement].
- Regressions and acceptance: [impact, decision and reviewer evidence; or none].

## Build and host identity

| Field | Previous Rust | Candidate | External reference, if applicable |
| --- | --- | --- | --- |
| Commit/version and source hash | [value] | [value; dirty source manifest if needed] | [name/version] |
| Executable path and SHA-256 | [value] | [value] | [value] |
| Lockfile SHA-256 | [value] | [value] | [applicability] |
| Toolchain/target/profile/features | [exact values] | [exact values] | [build identity] |
| Flags, LTO/codegen/strip settings | [values] | [values] | [known values/unknown] |

Host: [CPU, RAM, OS/kernel, architecture, filesystem/storage]. Environment:
[locale/timezone, relevant variables, concurrency/load controls, CPU/power settings
when controlled]. Tools: [Hyperfine and memory-tool versions, invocation mode].
Record unavailable metadata and uncontrolled factors.

## Workloads and correctness

| Case | Purpose/input shape | Size and checksum | Correctness evidence | Sink/access/cache/reset policy |
| --- | --- | --- | --- | --- |
| [ID] | [source/generator and exact arguments] | [counts/bytes/hash] | [bytes/status/stderr/effects plus independent checks] | [details] |

Link the executed checker and exact build/generation/measurement commands. Keep
setup and correctness checks outside timing unless explicitly part of the workload.
State which outputs differ intentionally and why they remain equivalent work.

## Elapsed time

| Case/configuration | Runs / warmups | Mean / median | Standard deviation | Candidate/baseline ratio | Verdict |
| --- | --- | --- | --- | --- | --- |
| [ID/binary/features/logging] | [counts] | [units] | [units] | [statistic and ratio] | [practical impact] |

Attach all raw samples. Record warnings/outliers and the second-batch order or
interleaving method. Explain sample-count exceptions, uncertainty, and remaining
startup/cache/storage limitations. Keep logging levels distinct.

## Executable size

| Build/configuration | Logical bytes | Delta bytes / percent | Strip policy | Artifact SHA-256 |
| --- | --- | --- | --- | --- |
| [ID] | [value] | [versus declared baseline] | [tool/settings] | [hash] |

State the file-size method and settings held constant. Report dependency-graph
and compilation evidence for optional dependencies. Do not infer runtime behavior
from file size. Record compressed/allocated sizes separately if measured.

## Memory and other resources

| Case/input scale | Tool/metric/native unit | Samples | Median peak RSS / range | Verdict |
| --- | --- | --- | --- | --- |
| [ID] | [details/conversion] | [count] | [units] | [scope and scaling limits] |

Link raw resource outputs. State buffer ownership, input scaling and platform
limitations. Record build times/allocations/other metrics separately or mark them
unmeasured; do not use elapsed time or binary size as a substitute.

## Validation and evidence retention

List exact executed package/workspace/feature check commands and results, with
native Linux/macOS scopes separated. Report unavailable checks and known failures.
Link raw artifacts and correctness tests; state retention/expiry, dataset replay
instructions, and source identity. Local-only paths are supplemental, not the sole
review evidence. Distinguish an automated CI gate from a manually followed procedure.
