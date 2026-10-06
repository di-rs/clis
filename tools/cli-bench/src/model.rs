use serde::{Deserialize, Deserializer, Serialize};
use std::collections::BTreeMap;

/// Executable roles in one comparison.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum Role {
    Reference,
    Previous,
    Candidate,
}

/// The fixed measurement policy, independent of Cargo's build profile.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum MeasurementProfile {
    Full,
    Smoke,
}

/// Per-role counts for checked warmups and separate elapsed/RSS observations.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MeasurementPolicy {
    pub warmups: u32,
    pub samples_per_batch: u32,
    pub batches: u32,
    pub rss_samples: u32,
    pub smoke_only: bool,
    pub performance_conclusions: bool,
}

impl MeasurementProfile {
    /// Return the frozen full or smoke sampling policy.
    #[must_use]
    pub const fn policy(self) -> MeasurementPolicy {
        match self {
            Self::Full => MeasurementPolicy {
                warmups: 3,
                samples_per_batch: 20,
                batches: 2,
                rss_samples: 5,
                smoke_only: false,
                performance_conclusions: true,
            },
            Self::Smoke => MeasurementPolicy {
                warmups: 1,
                samples_per_batch: 2,
                batches: 2,
                rss_samples: 1,
                smoke_only: true,
                performance_conclusions: false,
            },
        }
    }
}

/// Resource limits and explicit reasons for changing default deadlines.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Limits {
    pub max_cases: u64,
    pub max_stream_bytes: u64,
    pub max_generated_bytes: u64,
    pub max_generated_file_bytes: u64,
    pub max_evidence_bytes: u64,
    pub sample_timeout_seconds: u64,
    pub build_timeout_seconds: u64,
    pub timeout_reason: Option<String>,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_cases: 128,
            max_stream_bytes: 268_435_456,
            max_generated_bytes: 134_217_728,
            max_generated_file_bytes: 33_554_432,
            max_evidence_bytes: 2_147_483_648,
            sample_timeout_seconds: 120,
            build_timeout_seconds: 1800,
            timeout_reason: None,
        }
    }
}

/// Cargo stripping policy shared by Rust roles.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum StripPolicy {
    #[default]
    None,
    Debuginfo,
    Symbols,
}

/// Shared Rust build settings; release and locked builds are mandatory.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct BuildPolicy {
    pub toolchain: Option<String>,
    pub target: Option<String>,
    pub features: Vec<String>,
    pub no_default_features: bool,
    pub rustflags: Vec<String>,
    pub strip: StripPolicy,
}

/// Pinned Biggie source identity and generator-specific feature selection.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GeneratorSpec {
    pub revision: String,
    pub package: String,
    pub binary: String,
    #[serde(default)]
    pub features: Vec<String>,
    #[serde(default)]
    pub no_default_features: bool,
}

/// A complete profile-specific replacement for a dataset recipe.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DatasetRecipe {
    pub argv: Vec<String>,
    pub checks: Vec<CorrectnessRule>,
}

/// One deterministic Biggie output, identified independently of its profile.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DatasetSpec {
    pub id: String,
    pub argv: Vec<String>,
    pub output: String,
    pub checks: Vec<CorrectnessRule>,
    #[serde(default)]
    pub profiles: BTreeMap<MeasurementProfile, DatasetRecipe>,
}

// Scope these exceptions to a single enum and its derive-generated sibling items.
// Enum-item attributes alone do not cover Serde's generated anonymous consts.
pub use stdin_policy::StdinPolicy;

#[allow(
    clippy::empty_enums,
    reason = "Serde generates empty field-key enums for strict empty struct variants"
)]
mod stdin_policy {
    use serde::{Deserialize, Serialize};

    /// Explicit input boundary, with named generated datasets.
    #[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
    #[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
    pub enum StdinPolicy {
        Null {},
        RegularFile { dataset: String },
        Pipe { dataset: String },
    }
}

pub use stdout_policy::StdoutPolicy;

#[allow(
    clippy::empty_enums,
    reason = "Serde generates empty field-key enums for strict empty struct variants"
)]
mod stdout_policy {
    use serde::{Deserialize, Serialize};

    /// Explicit measured output boundary.
    #[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
    #[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
    pub enum StdoutPolicy {
        Discard {},
        DrainedPipe {},
        ScratchFile { path: String },
    }
}

/// Input/output boundaries shared by every role in a case.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IoPolicy {
    pub stdin: StdinPolicy,
    pub stdout: StdoutPolicy,
}

/// A captured byte stream.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum Stream {
    Stdout,
    Stderr,
}

/// Named targets require that role; selected baselines never invent a binding.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum ComparisonTarget {
    SelectedBaselines,
    Reference,
    Previous,
    Candidate,
}

/// Unit of an independently derived suffix of a generated dataset.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum TailUnit {
    Lines,
    Bytes,
}

pub use correctness_rule::CorrectnessRule;

#[allow(
    clippy::empty_enums,
    reason = "Serde generates empty field-key enums for strict empty struct variants"
)]
mod correctness_rule {
    use super::{ComparisonTarget, Stream, TailUnit};
    use serde::{Deserialize, Serialize};

    /// Closed independent correctness assertions, interpreted by later execution.
    #[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
    #[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
    pub enum CorrectnessRule {
        Comparator {
            target: ComparisonTarget,
            stream: Stream,
        },
        Literal {
            stream: Stream,
            text: String,
        },
        Hex {
            stream: Stream,
            hex: String,
        },
        EmptyStderr {},
        BytePattern {
            bytes: u64,
            pattern_hex: String,
        },
        TextShape {
            records: u64,
            words_per_record: u32,
            word_length: u32,
        },
        Records {
            records: Vec<String>,
            repeat: u64,
            cycles: u64,
        },
        TailSlice {
            dataset: String,
            unit: TailUnit,
            count: u64,
            stream: Stream,
        },
        DirectoryTree {
            paths: Vec<String>,
            #[serde(default)]
            compare_mode_to: Option<ComparisonTarget>,
        },
    }
}

pub use mutation_setup::MutationSetup;

#[allow(
    clippy::empty_enums,
    reason = "Serde generates empty field-key enums for strict empty struct variants"
)]
mod mutation_setup {
    use serde::{Deserialize, Serialize};

    /// Exact initial directory set under a later executor's owned scratch root.
    #[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
    #[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
    pub enum MutationSetup {
        None {},
        Directories { paths: Vec<String> },
    }
}

impl Default for MutationSetup {
    fn default() -> Self {
        Self::None {}
    }
}

/// Meaningful work units; seek-tail cases can omit a numerator.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum WorkUnit {
    Bytes,
    Records,
    DirectoryOperations,
}

/// Explicit numerator for a later throughput calculation.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Work {
    pub amount: u64,
    pub unit: WorkUnit,
}

/// Optional whole-field replacements; absent fields inherit the base case.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct CaseOverride {
    pub argv: Option<Vec<String>>,
    pub role_argv: Option<BTreeMap<Role, Vec<String>>>,
    pub correctness: Option<Vec<CorrectnessRule>>,
    pub work: Option<Work>,
}

/// One workload with explicit invocation, correctness and mutation boundaries.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CaseSpec {
    pub id: String,
    pub purpose: String,
    pub argv: Vec<String>,
    pub io: IoPolicy,
    pub expected_status: i32,
    pub correctness: Vec<CorrectnessRule>,
    #[serde(default)]
    pub role_argv: BTreeMap<Role, Vec<String>>,
    #[serde(default)]
    pub mutation: MutationSetup,
    pub work: Option<Work>,
    #[serde(default)]
    pub profiles: BTreeMap<MeasurementProfile, CaseOverride>,
}

/// Submitted UTF-8 v1 configuration; validation performs no I/O.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Suite {
    pub schema_version: u32,
    pub id: String,
    pub package: String,
    pub binary: String,
    pub generator: GeneratorSpec,
    pub datasets: Vec<DatasetSpec>,
    pub cases: Vec<CaseSpec>,
    #[serde(default)]
    pub build: BuildPolicy,
    #[serde(default)]
    pub limits: Limits,
    #[serde(
        default = "default_environment",
        deserialize_with = "deserialize_environment"
    )]
    pub environment: BTreeMap<String, String>,
}

fn default_environment() -> BTreeMap<String, String> {
    [("LC_ALL", "C"), ("TZ", "UTC"), ("CLIS_LOG_LEVEL", "off")]
        .into_iter()
        .map(|(key, value)| (key.into(), value.into()))
        .collect()
}

fn deserialize_environment<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<String, String>, D::Error> {
    let overrides = BTreeMap::deserialize(deserializer)?;
    let mut environment = default_environment();
    environment.extend(overrides);
    Ok(environment)
}

/// Streaming SHA-256 of file contents and logical byte length.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct FileIdentity {
    pub sha256: String,
    pub bytes: u64,
}

/// Explicitly resolved, release/locked build policy. No local paths in its identity.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResolvedBuildPolicy {
    pub compiler: String,
    pub cargo: String,
    pub target: String,
    pub settings: BuildPolicy,
    pub cargo_config_hashes: Vec<FileIdentity>,
    /// Normalized effective build environment, absent only for legacy/unknown records.
    #[serde(default)]
    pub environment_hash: Option<String>,
}

/// Provenance supplied by a verified build operation, never inferred from a prebuilt file.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BuildRecord {
    pub source_sha: String,
    pub lockfile: FileIdentity,
    pub policy: ResolvedBuildPolicy,
    pub command: Vec<String>,
    pub resolved_features: Vec<String>,
    /// Redacted Cargo compiler-artifact and dependency resolution evidence.
    #[serde(default)]
    pub evidence: Option<CargoEvidence>,
}

/// Allowlisted Cargo evidence; URL/config/environment secrets are retained only as hashes.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CargoEvidence {
    pub package: String,
    pub binary: String,
    pub artifact: serde_json::Value,
    pub dependencies: Vec<CargoDependency>,
    pub environment_hash: String,
}

/// One resolved package. Source URLs are hashed, never published verbatim.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CargoDependency {
    pub name: String,
    pub version: String,
    pub source_hash: Option<String>,
}

/// Immutable retained executable. Missing build data explicitly means unknown provenance.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactRecord {
    pub schema_version: u32,
    pub id: String,
    pub file: FileIdentity,
    pub build: Option<BuildRecord>,
}

/// Executable identity plus an observed version, independent of installation path.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ToolIdentity {
    pub file: FileIdentity,
    pub version: String,
}

/// Complete frozen experimental policy; candidate and selected cases are run bindings.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MeasurementContract {
    pub schema_version: u32,
    pub suite: Suite,
    pub harness: ToolIdentity,
    pub generator: ToolIdentity,
    pub engine: ToolIdentity,
    pub validator_policy: String,
    pub analysis_policy: String,
    pub build: ResolvedBuildPolicy,
    pub profile: MeasurementProfile,
}

/// Observable metadata; inability to probe is explicit, never an invented value.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(
    tag = "status",
    content = "value",
    rename_all = "kebab-case",
    deny_unknown_fields
)]
pub enum HostValue {
    Available(String),
    Unavailable(String),
}

/// Bounded platform observations and only allowlisted/redacted child settings.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HostMetadata {
    pub os: String,
    pub architecture: String,
    pub kernel: HostValue,
    pub cpu: HostValue,
    pub memory: HostValue,
    pub filesystem: HostValue,
    pub inherited_umask: HostValue,
    pub settings: BTreeMap<String, String>,
}

/// One actual input path and expected content identity, verified during finalization.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct InputRecord {
    pub dataset: String,
    pub path: std::path::PathBuf,
    pub file: FileIdentity,
}

/// Portable, complete identity of one profile's deterministic dataset recipe.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DatasetIdentity {
    pub schema_version: u32,
    pub dataset: String,
    pub output: String,
    pub profile: MeasurementProfile,
    pub generator: ArtifactRecord,
    pub generator_spec: GeneratorSpec,
    pub recipe: DatasetRecipe,
    /// Allowlisted settings only; isolated home/config physical paths are not recipe inputs.
    pub environment: BTreeMap<String, String>,
    pub path_records: bool,
}

/// Independently verified output and retained generation diagnostics.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GenerationRecord {
    pub identity: DatasetIdentity,
    pub recipe_hash: String,
    pub file: FileIdentity,
    /// Observed byte count for every independent shape assertion, in recipe order.
    pub shape_bytes: Vec<u64>,
    pub argv: Vec<String>,
    pub stdout: FileIdentity,
    pub stderr: FileIdentity,
}

/// Optional optimization metadata, retained even if resolution fails.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExperimentRequest {
    pub id: String,
    pub hypothesis: String,
    pub change_summary: String,
    pub requested_previous: String,
    pub requested_candidate: String,
}

/// Resolved run identities may be absent on early failure.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RunManifest {
    pub schema_version: u32,
    pub run_id: String,
    pub contract: Option<MeasurementContract>,
    pub roles: BTreeMap<Role, ArtifactRecord>,
    pub inputs: Vec<InputRecord>,
    pub selected_cases: Vec<String>,
    pub host: Option<HostMetadata>,
    pub tool_paths: Option<ToolPaths>,
    pub experiment: Option<ExperimentRequest>,
}

/// Terminal execution outcome; incomplete is also the initial on-disk state.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case", deny_unknown_fields)]
pub enum RunOutcome {
    Complete,
    Failed,
    Incomplete,
}

/// Evidence status. Measurement and analysis records are added by their owning stages.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RunResult {
    pub schema_version: u32,
    pub outcome: RunOutcome,
    pub message: Option<String>,
}

/// Typed append-only lifecycle event. Text must already be safe for publication.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "kebab-case",
    deny_unknown_fields
)]
pub enum RunEvent {
    Stage(String),
    Warning(String),
    Failure(String),
}

/// Immutable experiment anchor; written only after identity resolution succeeds.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExperimentRecord {
    pub schema_version: u32,
    pub starting_sha: String,
    pub contract: MeasurementContract,
    pub contract_id: String,
}

/// One invocation reference; its run status is authoritative even after interruption.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AttemptRecord {
    pub schema_version: u32,
    pub run_id: String,
    pub request: ExperimentRequest,
}

impl CargoEvidence {
    fn validate(&self, features: &[String]) -> Result<(), crate::BenchError> {
        crate::artifact::validate_identity(&FileIdentity {
            sha256: self.environment_hash.clone(),
            bytes: 0,
        })?;
        for dependency in &self.dependencies {
            if let Some(hash) = &dependency.source_hash {
                crate::artifact::validate_identity(&FileIdentity {
                    sha256: hash.clone(),
                    bytes: 0,
                })?;
            }
        }
        if self.package.is_empty()
            || self.binary.is_empty()
            || self
                .artifact
                .get("reason")
                .and_then(serde_json::Value::as_str)
                != Some("compiler-artifact")
            || self
                .artifact
                .pointer("/target/name")
                .and_then(serde_json::Value::as_str)
                != Some(self.binary.as_str())
            || self.artifact.get("features") != Some(&serde_json::to_value(features)?)
        {
            return Err(crate::BenchError::Evidence(
                "inconsistent Cargo artifact evidence".into(),
            ));
        }
        Ok(())
    }
}
impl BuildRecord {
    pub(crate) fn validate(&self) -> Result<(), crate::BenchError> {
        validate_sha(&self.source_sha)?;
        crate::artifact::validate_identity(&self.lockfile)?;
        self.policy.validate()?;
        if let Some(evidence) = &self.evidence {
            evidence.validate(&self.resolved_features)?;
            if self.policy.environment_hash.as_ref() != Some(&evidence.environment_hash) {
                return Err(crate::BenchError::Evidence(
                    "Cargo evidence and build policy environments differ".into(),
                ));
            }
        }
        if self.command.is_empty() || self.command.iter().any(|arg| arg.contains('\0')) {
            return Err(crate::BenchError::Evidence("invalid build command".into()));
        }
        Ok(())
    }
}
impl ResolvedBuildPolicy {
    pub(crate) fn validate(&self) -> Result<(), crate::BenchError> {
        for text in [&self.compiler, &self.cargo, &self.target] {
            if text.trim().is_empty() || text.contains('\0') {
                return Err(crate::BenchError::Evidence(
                    "unresolved build policy".into(),
                ));
            }
        }
        if let Some(hash) = &self.environment_hash {
            crate::artifact::validate_identity(&FileIdentity {
                sha256: hash.clone(),
                bytes: 0,
            })?;
        }
        for identity in &self.cargo_config_hashes {
            crate::artifact::validate_identity(identity)?;
        }
        Ok(())
    }
}
fn validate_sha(sha: &str) -> Result<(), crate::BenchError> {
    if sha.len() != 40 || !sha.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(crate::BenchError::Evidence(
            "expected a full source SHA".into(),
        ));
    }
    Ok(())
}
impl MeasurementContract {
    /// Stable identity over the complete declared suite and resolved tool/build policies.
    /// Actual paths, selected cases and candidate identity are intentionally run bindings.
    ///
    /// # Errors
    /// Rejects unresolved/invalid policies, identities and suites.
    pub fn identity(&self) -> Result<String, crate::BenchError> {
        if self.schema_version != 1 {
            return Err(crate::BenchError::Evidence(
                "unsupported contract schema".into(),
            ));
        }
        crate::validate_suite(&self.suite)?;
        self.build.validate()?;
        let mut resolved = self.suite.clone();
        resolved.build.clone_from(&self.build.settings);
        crate::validate_suite(&resolved)?;
        for tool in [&self.harness, &self.generator, &self.engine] {
            crate::artifact::validate_identity(&tool.file)?;
            if tool.version.trim().is_empty() {
                return Err(crate::BenchError::Evidence("missing tool version".into()));
            }
        }
        if self.validator_policy.is_empty() || self.analysis_policy.is_empty() {
            return Err(crate::BenchError::Evidence(
                "missing validation/analysis policy".into(),
            ));
        }
        crate::artifact::json_identity(self)
    }
}

/// Actual paths whose bytes must match the frozen tool identities at finalization.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ToolPaths {
    pub harness: std::path::PathBuf,
    pub generator: std::path::PathBuf,
    pub engine: std::path::PathBuf,
}

#[cfg(test)]
mod tests {
    use super::{MeasurementPolicy, MeasurementProfile};

    #[test]
    fn generation_limits_default_to_small_inputs_and_allow_explicit_overrides()
    -> Result<(), Box<dyn std::error::Error>> {
        let limits = super::Limits::default();
        if limits.max_generated_file_bytes != 33_554_432
            || limits.max_generated_bytes != 134_217_728
        {
            return Err("unexpected default dataset budgets".into());
        }
        let custom: super::Limits =
            toml::from_str("max_generated_file_bytes = 64\nmax_generated_bytes = 128\n")?;
        if custom.max_generated_file_bytes != 64 || custom.max_generated_bytes != 128 {
            return Err("explicit limits not preserved".into());
        }
        Ok(())
    }

    #[test]
    fn dataset_recipe_roundtrip_retains_profile_specific_seed_and_rejects_unknown_fields()
    -> Result<(), Box<dyn std::error::Error>> {
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let spec = suite.datasets.first().ok_or("missing fixture dataset")?;
        let recipe = super::DatasetRecipe {
            argv: spec.argv.clone(),
            checks: spec.checks.clone(),
        };
        let encoded = serde_json::to_value(&recipe)?;
        if serde_json::from_value::<super::DatasetRecipe>(encoded.clone())? != recipe {
            return Err("recipe changed during serialization".into());
        }
        let mut unknown = encoded;
        unknown["assume_deterministic"] = serde_json::json!(true);
        if serde_json::from_value::<super::DatasetRecipe>(unknown).is_ok() {
            return Err("unknown recipe field accepted".into());
        }
        Ok(())
    }

    #[test]
    fn cargo_evidence_rejects_unresolved_hashes_and_mismatched_binary_features()
    -> Result<(), Box<dyn std::error::Error>> {
        let evidence = super::CargoEvidence {
            package: "tiny".into(),
            binary: "tiny".into(),
            dependencies: vec![],
            environment_hash: "a".repeat(64),
            artifact: serde_json::json!({"reason":"compiler-artifact", "target":{"name":"tiny"}, "features":["fast"]}),
        };
        evidence.validate(&["fast".into()])?;
        if evidence.validate(&[]).is_ok() {
            return Err("changed resolved features accepted".into());
        }
        let mut changed = evidence.clone();
        changed.environment_hash = "unknown".into();
        if changed.validate(&["fast".into()]).is_ok() {
            return Err("invalid environment identity accepted".into());
        }
        let mut changed = evidence;
        changed.binary = "other".into();
        if changed.validate(&["fast".into()]).is_ok() {
            return Err("wrong binary artifact accepted".into());
        }
        Ok(())
    }

    #[test]
    fn resolved_policy_rejects_malformed_environment_identity()
    -> Result<(), Box<dyn std::error::Error>> {
        let policy = super::ResolvedBuildPolicy {
            compiler: "compiler".into(),
            cargo: "cargo".into(),
            target: "target".into(),
            settings: super::BuildPolicy::default(),
            cargo_config_hashes: vec![],
            environment_hash: Some("unknown".into()),
        };
        if policy.validate().is_ok() {
            return Err("invalid policy environment hash accepted".into());
        }
        Ok(())
    }

    #[test]
    fn full_policy_requires_checked_forward_reverse_observations() {
        assert_eq!(
            MeasurementProfile::Full.policy(),
            MeasurementPolicy {
                warmups: 3,
                samples_per_batch: 20,
                batches: 2,
                rss_samples: 5,
                smoke_only: false,
                performance_conclusions: true,
            }
        );
    }

    #[test]
    fn smoke_policy_limits_work_and_suppresses_performance_conclusions() {
        assert_eq!(
            MeasurementProfile::Smoke.policy(),
            MeasurementPolicy {
                warmups: 1,
                samples_per_batch: 2,
                batches: 2,
                rss_samples: 1,
                smoke_only: true,
                performance_conclusions: false,
            }
        );
    }
    #[test]
    fn early_failure_fixture_preserves_absent_identities_and_is_strict()
    -> Result<(), Box<dyn std::error::Error>> {
        let text = include_str!("../tests/inputs/failed-manifest.json");
        let manifest: super::RunManifest = serde_json::from_str(text)?;
        if manifest.contract.is_some() || !manifest.roles.is_empty() || manifest.host.is_some() {
            return Err("early failure fabricated provenance".into());
        }
        let mut json = serde_json::to_value(manifest)?;
        json["unexpected"] = serde_json::json!(true);
        if serde_json::from_value::<super::RunManifest>(json).is_ok() {
            return Err("unknown manifest field accepted".into());
        }
        let mut json: serde_json::Value = serde_json::from_str(text)?;
        json["experiment"]["unexpected"] = serde_json::json!(true);
        if serde_json::from_value::<super::RunManifest>(json).is_ok() {
            return Err("unknown experiment field accepted".into());
        }
        Ok(())
    }
}
