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
            max_generated_bytes: 8_589_934_592,
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

#[cfg(test)]
mod tests {
    use super::{MeasurementPolicy, MeasurementProfile};

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
}
