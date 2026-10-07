# PublicationEnvelope v1

This is the data-only interface between the untrusted `cli-bench.yml` producer
and the separately trusted publisher. One `publication.json` per platform,
aggregate maximum **16,777,216 encoded bytes**, including its final newline.
Artifact names are `publication-linux` and `publication-macos`; smoke artifacts
have a `smoke-` prefix. Full evidence is separate and is never publisher input.

## Envelope

All fields are required. Unknown fields, duplicate JSON object keys, non-finite
numbers, missing/duplicate suites, and unsupported versions are errors.

| Field | Type and constraints |
| --- | --- |
| `schema_version` | Integer exactly 1; booleans are not integers. |
| `repository` | `owner/repo`, each component 1–100 ASCII letters/digits/underscore/dot/hyphen. Untrusted claim. |
| `run_id` | Positive decimal string, 1–20 digits. Untrusted claim. |
| `run_attempt` | Integer 1–1,000,000. Untrusted claim. |
| `platform` | `linux` or `macos`; observations are never combined across platforms. |
| `measurement_profile` | `full` or `smoke`, including when no contract was sealed. |
| `candidate_sha`, `target_sha`, `previous_sha` | Nonzero lowercase 40-hex SHA or null when unavailable. Push target is null. Sealed records require candidate/previous selection. |
| `requested_retention_days` | Integer exactly 90. |
| `expires_at_unix_seconds` | Producer always supplies null. Only the trusted publisher may associate API-confirmed effective expiry. |
| `suites` | Exactly three entries, packages `biggie`, `tailr`, `mkdirr`, once each. |
| `omissions` | Array of at most 128 strings, each at most 4,096 UTF-8 bytes. |

Each suite entry has exactly `package`, `records` and `failure`. `records` is an
array containing **one unchanged HistoryRecord v1** when a run was sealed, or
zero records with an explicit failure. A sealed failed run is retained together
with the command failure. No record and a null failure is invalid.

`failure` is null or an object with exactly:

- `stage`: `selection`, `setup`, `build`, `measurement`, `projection`, or `artifact`.
- `message`: string, maximum 4,096 UTF-8 bytes (producer emits at most 1,024 characters).
- `exit_code`: integer −255 through 255, or null when no command status is available.

The original HistoryRecord retains `publication`, submitted/resolved suite text,
checksums, generations, tools, pipeline, experiment, attempt, diagnostic prefixes,
and omissions. It is defined by [history.rs](../src/history.rs), with its sole
normalized observations/analysis inside [PublicationRecord](../src/report.rs).
There is no second statistics schema and no Python statistical calculation.
Both suite-text fields are bounded to 8 MiB each as well as the aggregate bound.
All records must declare `execution_kind: "measure"`. A resolved contract must
match the envelope profile and suite package. Failed records with `contract: null`
remain valid; the envelope supplies their requested profile. A check-only record
must never be promoted as a measurement. Failed/incomplete records require a
failure entry even when the offline renderer itself returned success.

The producer obtains records through the existing `cli-bench history -f json`,
which validates sealed evidence using Rust. Its Python validator checks the
wrapper, HistoryRecord/PublicationRecord/manifest field sets, profile, suite and
outcome alignment. **This producer validation is not a trusted, complete nested
schema validator.** Task 13 must independently validate every nested Rust model,
normalized observations and historical metadata from untrusted bytes. Reuse the
existing schemas rather than accepting arbitrary nested objects or executing the
candidate's validator. Cargo's retained compiler-artifact evidence intentionally
has its existing JSON-value field; do not interpret it as code.

## Failure and retention

Selection failure, missing parent or setup/build failure can produce no sealed
run. Preserve all three suite failure entries rather than omitting that platform.
A nonzero measurement command remains a failure even if rendering/export succeeds.
If export fails, retain the small publication and the export omission. Full
evidence requests 90 days and is capped at 512 MiB aggregate, reserving 16 MiB
for publication. CI exports omit inputs and binaries, retain other sealed evidence,
and retain up to 1 MiB per command log. Original local resources are not deleted.
Full evidence is untrusted and replay still needs exact original executables.

Full export uses a separate bounded local inventory (at most three regular run
directories, matching manifest run IDs and package/suite IDs). Inventory metadata
reads have fixed byte limits and reject symlinks. The existing Rust exporter
still verifies each sealed bundle. An oversized individual history response or
failed history command therefore cannot skip available safe run paths or their
omitted-ID diagnostics.

If individual or combined history exceeds 16 MiB or projection validation fails, fail the job
and emit a small **projection-failure envelope**: zero records, explicit failure
for every suite, and omitted run IDs/reason in `omissions`. Retain the available
full bundles independently. Never silently truncate suite text or observations,
or call partial publication a success. A trusted publisher can preserve this
envelope-level failure; omitted original HistoryRecords are unavailable for
compact history. Missing uploads, early interpreter/checkout failure, cancellation
or runner loss can still mean no artifact exists and must remain visible.

Repository, workflow/run/attempt identity, PR association, current head and artifact
expiry must be obtained independently from the GitHub API. No PR number, trusted
URL, promotion authority or expiry is inferred from artifact content. The producer
never posts comments, updates history branches or executes downloaded artifacts.

## Fixtures and verification

`fixtures/setup-history.json` is an original CLI `history -f json` output from a
tiny `biggie` fixture with an intentionally missing Hyperfine path. It preserves
a real sealed `measure` failure with no resolved contract; it is not a benchmark.
`fixtures/setup-envelope.json` wraps it with synthetic workflow identity and two
explicit unsealed failures. No reference implementation code or external fixture
was copied. Python tests validate both and exercise three-suite aggregate overflow,
export retention, unsafe input rejection and real temporary Git graphs.

Action/tool references verified from primary upstream tag APIs on 2026-10-07:

- [checkout v7.0.1](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1)
- [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/tree/cf430e030ddbb5b0abf93d22962f4752f3646cd9)
- [install-action v2.87.25](https://github.com/taiki-e/install-action/tree/183e4297cca2404691e9380e1307288dced5c82a)
- [setup-uv v10.2.0](https://github.com/astral-sh/setup-uv/tree/c18668ad3cf93ea998bef934396af7bb5c839dc7), uv 0.12.17
- [Hyperfine v1.20.0](https://github.com/sharkdp/hyperfine/tree/975fe108c4ee7bd2600d10758207b44ca3dae738)
- [Rust nightly 2026-10-04 manifest](https://static.rust-lang.org/dist/2026-10-04/channel-rust-nightly.toml), local rustc `db8f076d2` (2026-10-03)

Hosted operation, actual effective retention and Linux/macOS measurements require
separate acceptance evidence; a local linter pass does not establish them.
