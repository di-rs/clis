# Pydantic for non-domain Python data

The repository uses dataclasses for several different purposes. This change puts validation at configuration and data boundaries while preserving the behavior of domain values and process-owned state.

## Scope

Boxie operator configuration (`OrchestratorConfig` and `RuntimeConfig`) becomes Pydantic Settings. Their JSON files remain the only source of operator values. Environment variables, dotenv files, and secret directories must not silently override those files. Nested `LifecycleConfig` and `ApiConfig` become frozen Pydantic models. Credential files still pass through `read_credential`; settings errors must not expose credential bytes.

The file loaders report validation failures as field locations and error types. Pydantic's structured validation errors retain raw inputs even when formatted messages hide them, so raw validation exceptions do not cross a file loader boundary after credentials are read.

Other immutable non-domain records become Pydantic models where validation of constructed or parsed data is useful: Boxie admission and artifact limits, telemetry settings and events, SQL and Docker adapter results, ZIP metadata, and Temporal workflow payloads; benchmark selection and report records; and Cargo workspace discovery records. Existing field names and serialized JSON shapes stay stable. Domain dataclasses in `boxie.domain` are unchanged.

Temporal payloads require the SDK's Pydantic data converter on the production client and on tests that construct clients directly. Existing JSON payloads must remain readable before the workflow contracts change. Workflow sandbox imports must remain deterministic.

## Dataclasses retained

The inventory on `origin/master` contains 45 dataclass classes. Thirteen are in `boxie.domain` and remain domain models. Five are test fixtures. `LogStats`, `Deadlines`, and `ExecutionRuntime` hold mutable process state. `OutputFrame` is created on the terminal hot path. `Diagnostic` supplies ordering for CLI output. `HostResources` and `OperatorResources` group Pulumi resource objects. These 25 classes remain dataclasses because Pydantic validation would add no useful boundary check or would change their operational behavior. The other 20 are migration candidates.

## Compatibility and validation

Use frozen models for immutable values, forbid extra input fields at untrusted boundaries, and preserve existing numeric bounds and clear error behavior. Replace positional model construction with named fields. Preserve the defensive copy of `ApiConfig.consumers` and suppress secret values in representations and validation errors. No implicit environment source is introduced for Boxie configuration. Existing domain-to-wire adapters remain explicit.

Verification covers model construction and invalid input, JSON configuration isolation, Temporal payload round trips, CLI process output, Python strict mypy and Ruff checks, and package tests. New CLI coverage follows the repository's end-to-end test conventions.
