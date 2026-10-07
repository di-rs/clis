use crate::{BenchError, Role, RssSample, RunManifest, TimingSample};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Explicit roles selected inside one saved run; no historical denominator.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ComparisonSelection {
    pub baseline: Role,
    pub candidate: Role,
}

pub fn select_comparison(
    analysis: &mut Analysis,
    manifest: &RunManifest,
    selection: ComparisonSelection,
) -> Result<(), BenchError> {
    let baseline = manifest.roles.get(&selection.baseline);
    let candidate = manifest.roles.get(&selection.candidate);
    if selection.baseline == selection.candidate {
        return Err(BenchError::Evidence(
            "comparison requires two distinct roles".into(),
        ));
    }
    let (Some(baseline), Some(candidate)) = (baseline, candidate) else {
        return Err(BenchError::Evidence(
            "selected comparison role is unavailable in this run".into(),
        ));
    };
    for case in &mut analysis.cases {
        let base = case
            .roles
            .get(&selection.baseline)
            .ok_or_else(|| BenchError::Evidence("baseline metrics unavailable".into()))?;
        let cand = case
            .roles
            .get(&selection.candidate)
            .ok_or_else(|| BenchError::Evidence("candidate metrics unavailable".into()))?;
        case.comparisons = vec![compare(
            selection,
            base,
            cand,
            baseline,
            candidate,
            analysis.smoke_only,
        )];
        case.roles
            .retain(|role, _| *role == selection.baseline || *role == selection.candidate);
    }
    Ok(())
}
/// Descriptive statistics, with N-1 sample standard deviation.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Statistics {
    pub count: u32,
    pub mean: f64,
    pub median: f64,
    pub sample_stddev: Option<f64>,
    pub min: f64,
    pub max: f64,
}
/// Observed direction is advisory; smoke does not support a conclusion.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Direction {
    Faster,
    Slower,
    Equal,
    Inconclusive,
    SmokeOnly,
    Unavailable,
}
/// Provenance constrains what can be attributed to a source change.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Comparison {
    pub baseline: Role,
    pub candidate: Role,
    pub scope: String,
    pub limitations: Vec<String>,
    pub ratio: Option<f64>,
    pub elapsed_change_seconds: Option<f64>,
    pub elapsed_change_percent: Option<f64>,
    pub batch_ratios: BTreeMap<crate::TimingBatch, f64>,
    pub direction: Direction,
    pub size_change_bytes: i128,
    pub size_change_percent: Option<f64>,
}
/// Validated descriptive measurements for a role. Missing data stays unavailable.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RoleAnalysis {
    pub batches: BTreeMap<crate::TimingBatch, Statistics>,
    pub elapsed: Option<Statistics>,
    pub rss: Option<Statistics>,
    pub executable_bytes: u64,
    pub throughput: Option<f64>,
    pub issues: Vec<String>,
}
/// One case is compared only within its own selected profile and I/O contract.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaseAnalysis {
    pub id: String,
    pub purpose: String,
    pub confirmation: bool,
    pub work: Option<crate::Work>,
    pub io: crate::IoPolicy,
    pub roles: BTreeMap<Role, RoleAnalysis>,
    pub comparisons: Vec<Comparison>,
}
/// Deterministic descriptive-v1 output. Raw observations are never discarded.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Analysis {
    pub policy: String,
    pub contract_id: Option<String>,
    pub smoke_only: bool,
    pub cases: Vec<CaseAnalysis>,
    pub timing_samples: Vec<TimingSample>,
    pub rss_samples: Vec<RssSample>,
    pub issues: Vec<String>,
}
/// Analyze saved observations without executing tools or reading process state.
/// # Errors
/// Rejects malformed manifest identities or an unsupported analysis contract.
pub fn analyze(
    manifest: &RunManifest,
    timing: &[TimingSample],
    rss: &[RssSample],
) -> Result<Analysis, BenchError> {
    let mut analysis = Analysis {
        policy: "descriptive-v1".into(),
        contract_id: None,
        smoke_only: false,
        cases: vec![],
        timing_samples: timing.to_vec(),
        rss_samples: rss.to_vec(),
        issues: vec![],
    };
    let Some(contract) = &manifest.contract else {
        analysis
            .issues
            .push("measurement contract unavailable".into());
        return Ok(analysis);
    };
    if contract.analysis_policy != "descriptive-v1" || manifest.schema_version != 1 {
        return Err(BenchError::Evidence(
            "unsupported analysis/manifest policy".into(),
        ));
    }
    analysis.contract_id = Some(contract.identity()?);
    analysis.smoke_only = contract.profile.policy().smoke_only;
    let selected: std::collections::BTreeSet<_> = manifest.selected_cases.iter().collect();
    if selected.len() != manifest.selected_cases.len()
        || selected
            .iter()
            .any(|id| !contract.suite.cases.iter().any(|c| &c.id == *id))
    {
        return Err(BenchError::Evidence(
            "invalid analysis case selection".into(),
        ));
    }
    let unknown = timing
        .iter()
        .any(|s| !selected.contains(&s.case) || !manifest.roles.contains_key(&s.role))
        || rss
            .iter()
            .any(|s| !selected.contains(&s.case) || !manifest.roles.contains_key(&s.role));
    if unknown {
        analysis
            .issues
            .push("unmatched role or case samples".into());
    }
    for id in selected {
        let case = contract
            .suite
            .cases
            .iter()
            .find(|case| &case.id == id)
            .ok_or_else(|| BenchError::Evidence("missing case".into()))?;
        let resolved = case.effective(contract.profile);
        let mut roles = BTreeMap::new();
        for (role, artifact) in &manifest.roles {
            let identity = crate::SampleIdentity::new(
                &manifest.run_id,
                case,
                contract.profile,
                &manifest.inputs,
                &artifact.id,
            )?;
            let mut measured =
                analyze_role(*role, case, &identity, timing, rss, artifact.file.bytes);
            if unknown {
                measured.elapsed = None;
                measured.issues.push("unmatched sample set".into());
            }
            measured.throughput = resolved
                .work
                .as_ref()
                .zip(measured.elapsed.as_ref())
                .and_then(|(work, stats)| positive_divide(number(work.amount), stats.mean));
            roles.insert(*role, measured);
        }
        if !case.equal_dataset_operands(contract.profile, manifest.roles.keys().copied()) {
            for measured in roles.values_mut() {
                measured.elapsed = None;
                measured.throughput = None;
                measured
                    .issues
                    .push("unequal dataset operands across roles".into());
            }
        }
        let comparisons = comparisons_for(&roles, &manifest.roles, analysis.smoke_only);
        analysis.cases.push(CaseAnalysis {
            id: id.clone(),
            purpose: case.purpose.clone(),
            confirmation: id.starts_with("confirm-"),
            work: resolved.work,
            io: case.io.clone(),
            roles,
            comparisons,
        });
    }
    Ok(analysis)
}
fn comparisons_for(
    roles: &BTreeMap<Role, RoleAnalysis>,
    artifacts: &BTreeMap<Role, crate::ArtifactRecord>,
    smoke: bool,
) -> Vec<Comparison> {
    let mut comparisons = vec![];
    if let (Some(candidate), Some(candidate_record)) =
        (roles.get(&Role::Candidate), artifacts.get(&Role::Candidate))
    {
        for baseline in [Role::Reference, Role::Previous] {
            if let (Some(base), Some(base_record)) =
                (roles.get(&baseline), artifacts.get(&baseline))
            {
                comparisons.push(compare(
                    ComparisonSelection {
                        baseline,
                        candidate: Role::Candidate,
                    },
                    base,
                    candidate,
                    base_record,
                    candidate_record,
                    smoke,
                ));
            }
        }
    }
    comparisons
}
fn analyze_role(
    role: Role,
    case: &crate::CaseSpec,
    identity: &crate::SampleIdentity,
    timing: &[TimingSample],
    rss: &[RssSample],
    executable_bytes: u64,
) -> RoleAnalysis {
    let samples: Vec<_> = timing
        .iter()
        .filter(|s| s.case == case.id && s.role == role)
        .collect();
    let mut result = RoleAnalysis {
        batches: BTreeMap::new(),
        elapsed: None,
        rss: None,
        executable_bytes,
        throughput: None,
        issues: vec![],
    };
    let policy = identity.profile.policy();
    let valid = samples
        .iter()
        .all(|s| s.identity == *identity && s.status == case.expected_status);
    if valid {
        for batch in [crate::TimingBatch::Forward, crate::TimingBatch::Reverse] {
            let observations: Vec<_> = samples.iter().filter(|s| s.batch == batch).collect();
            let ordinals: std::collections::BTreeSet<_> =
                observations.iter().map(|s| s.ordinal).collect();
            if observations.len() == usize::try_from(policy.samples_per_batch).unwrap_or(usize::MAX)
                && ordinals == (1..=policy.samples_per_batch).collect()
                && let Some(stats) =
                    statistics(&observations.iter().map(|s| s.seconds).collect::<Vec<_>>())
            {
                result.batches.insert(batch, stats);
            }
        }
        if result.batches.len() == 2 {
            result.elapsed = statistics(&samples.iter().map(|s| s.seconds).collect::<Vec<_>>());
        }
    }
    if result.elapsed.is_none() {
        result
            .issues
            .push("elapsed unavailable: incomplete, invalid or unmatched batches".into());
    }
    let memory: Vec<_> = rss
        .iter()
        .filter(|s| s.case == case.id && s.role == role)
        .collect();
    let ordinals: std::collections::BTreeSet<_> = memory.iter().map(|s| s.ordinal).collect();
    let same_scope = memory.first().is_some_and(|first| {
        memory.iter().all(|s| {
            s.identity == *identity
                && s.status == case.expected_status
                && s.rss.platform == first.rss.platform
                && s.rss.unit == first.rss.unit
                && s.rss.accounting_scope == first.rss.accounting_scope
                && s.invocation_scope == first.invocation_scope
                && match s.rss.unit {
                    crate::RssUnit::Bytes => s.rss.bytes == s.rss.value,
                    crate::RssUnit::Kibibytes => s.rss.value.checked_mul(1024) == Some(s.rss.bytes),
                }
        })
    });
    if same_scope
        && memory.len() == usize::try_from(policy.rss_samples).unwrap_or(usize::MAX)
        && ordinals == (1..=policy.rss_samples).collect()
    {
        result.rss = descriptive_statistics(
            &memory
                .iter()
                .map(|s| number(s.rss.bytes))
                .collect::<Vec<_>>(),
        );
    }
    if result.rss.is_none() {
        result
            .issues
            .push("RSS unavailable: incomplete, invalid or unmatched samples".into());
    }
    result
}
fn compare(
    selection: ComparisonSelection,
    base: &RoleAnalysis,
    candidate: &RoleAnalysis,
    base_record: &crate::ArtifactRecord,
    candidate_record: &crate::ArtifactRecord,
    smoke: bool,
) -> Comparison {
    let (scope, limitations) = comparison_scope(selection, base_record, candidate_record);
    let changes = base.rss.as_ref().zip(candidate.rss.as_ref()).and_then(|_| {
        base.elapsed
            .as_ref()
            .zip(candidate.elapsed.as_ref())
            .and_then(|(b, c)| relative_change(b.mean, c.mean))
    });
    let mut batch_ratios: BTreeMap<_, _> = base
        .batches
        .iter()
        .filter_map(|(batch, b)| {
            candidate
                .batches
                .get(batch)
                .and_then(|c| relative_change(b.mean, c.mean))
                .map(|change| (*batch, change.0))
        })
        .collect();
    if changes.is_none() {
        batch_ratios.clear();
    }
    let direction = if changes.is_none() {
        Direction::Unavailable
    } else if smoke {
        Direction::SmokeOnly
    } else if batch_ratios.values().all(|v| *v < 1.0) {
        Direction::Faster
    } else if batch_ratios.values().all(|v| *v > 1.0) {
        Direction::Slower
    } else if batch_ratios.values().all(|v| v.total_cmp(&1.0).is_eq()) {
        Direction::Equal
    } else {
        Direction::Inconclusive
    };
    Comparison {
        baseline: selection.baseline,
        candidate: selection.candidate,
        scope,
        limitations,
        ratio: changes.map(|v| v.0),
        elapsed_change_seconds: changes.map(|v| v.1),
        elapsed_change_percent: changes.map(|v| v.2),
        batch_ratios,
        direction,
        size_change_bytes: i128::from(candidate.executable_bytes)
            .saturating_sub(i128::from(base.executable_bytes)),
        size_change_percent: relative_change(
            number(base.executable_bytes),
            number(candidate.executable_bytes),
        )
        .map(|v| v.2),
    }
}
fn comparison_scope(
    selection: ComparisonSelection,
    base: &crate::ArtifactRecord,
    candidate: &crate::ArtifactRecord,
) -> (String, Vec<String>) {
    if ![selection.baseline, selection.candidate].contains(&Role::Reference)
        && let (Some(b), Some(c)) = (&base.build, &candidate.build)
    {
        if b.policy == c.policy && b.policy.environment_hash.is_some() {
            return (
                "source-isolated comparison".into(),
                vec!["observational direction; no significance or equivalence claim".into()],
            );
        }
        return ("product comparison".into(), vec!["build policies differ or build environment provenance is unknown; source effects are not isolated".into()]);
    }
    (
        "product comparison".into(),
        vec![
            "reference products or unknown prebuilt provenance; source effects are not isolated"
                .into(),
        ],
    )
}
#[allow(
    clippy::cast_precision_loss,
    clippy::as_conversions,
    reason = "descriptive floating metrics may round; original byte counts and integer deltas remain exact"
)]
const fn number(value: u64) -> f64 {
    value as f64
}
#[allow(
    clippy::arithmetic_side_effects,
    reason = "checked positive finite denominator and checked result"
)]
fn positive_divide(amount: f64, seconds: f64) -> Option<f64> {
    if seconds <= 0.0 {
        return None;
    }
    let result = amount / seconds;
    result.is_finite().then_some(result)
}
fn statistics(values: &[f64]) -> Option<Statistics> {
    if values.iter().any(|v| *v <= 0.0) {
        return None;
    }
    descriptive_statistics(values)
}
#[allow(
    clippy::arithmetic_side_effects,
    reason = "nonnegative finite samples, checked count and finite result validation bound floating point arithmetic"
)]
fn descriptive_statistics(values: &[f64]) -> Option<Statistics> {
    if values.is_empty() || values.iter().any(|v| !v.is_finite() || *v < 0.0) {
        return None;
    }
    let count = u32::try_from(values.len()).ok()?;
    let n = f64::from(count);
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let mean: f64 = sorted.iter().map(|v| v / n).sum();
    let midpoint = sorted.len().checked_div(2)?;
    let median = if count.is_multiple_of(2) {
        sorted.get(midpoint.checked_sub(1)?)? / 2.0 + sorted.get(midpoint)? / 2.0
    } else {
        *sorted.get(midpoint)?
    };
    let sample_stddev = if count > 1 {
        Some(
            (sorted
                .iter()
                .map(|v| (v - mean).powi(2) / (n - 1.0))
                .sum::<f64>())
            .sqrt(),
        )
    } else {
        None
    };
    if !mean.is_finite() || sample_stddev.is_some_and(|v| !v.is_finite()) {
        return None;
    }
    Some(Statistics {
        count,
        mean,
        median,
        sample_stddev,
        min: *sorted.first()?,
        max: *sorted.last()?,
    })
}
#[allow(
    clippy::arithmetic_side_effects,
    reason = "positive finite operands and finite checked results; zero denominators rejected"
)]
fn relative_change(baseline: f64, candidate: f64) -> Option<(f64, f64, f64)> {
    if !baseline.is_finite() || !candidate.is_finite() || baseline <= 0.0 || candidate <= 0.0 {
        return None;
    }
    let ratio = candidate / baseline;
    let absolute = candidate - baseline;
    let percent = (ratio - 1.0) * 100.0;
    (ratio.is_finite() && absolute.is_finite() && percent.is_finite())
        .then_some((ratio, absolute, percent))
}

#[cfg(test)]
mod tests {
    use super::*;
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    #[test]
    fn exact_saved_arithmetic_and_sample_standard_deviation() -> TestResult {
        let baseline = statistics(&[2.0, 2.0]).ok_or("missing baseline")?;
        let candidate = statistics(&[1.0, 1.0]).ok_or("missing candidate")?;
        crate::test_support::equal(
            &(relative_change(baseline.mean, candidate.mean)),
            &(Some((0.5, -1.0, -50.0))),
        )?;
        let spread = statistics(&[1.0, 2.0, 3.0]).ok_or("missing spread")?;
        crate::test_support::equal(&(spread.sample_stddev), &(Some(1.0)))?;
        crate::test_support::equal(&(spread.median, spread.min, spread.max), &(2.0, 1.0, 3.0))?;
        crate::test_support::equal(&(statistics(&[4.0]).and_then(|s| s.sample_stddev)), &(None))?;
        Ok(())
    }
    #[test]
    fn zero_nonfinite_denominators_and_invalid_samples_have_no_ratio() -> TestResult {
        for value in [0.0, -1.0, f64::INFINITY, f64::NAN] {
            crate::test_support::equal(&(relative_change(value, 1.0)), &(None))?;
            crate::test_support::equal(&(relative_change(1.0, value)), &(None))?;
            crate::test_support::equal(&(statistics(&[value])), &(None))?;
        }
        crate::test_support::equal(&(statistics(&[])), &(None))?;
        Ok(())
    }
    fn fixture(
        profile: crate::MeasurementProfile,
    ) -> Result<(RunManifest, Vec<TimingSample>, Vec<RssSample>), BenchError> {
        use crate::*;
        let suite = parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let file = FileIdentity {
            sha256: "a".repeat(64),
            bytes: 10,
        };
        let tool = ToolIdentity {
            file: file.clone(),
            version: "fixture".into(),
        };
        let manifest = RunManifest {
            schema_version: 1,
            execution_kind: None,
            run_id: "saved-run".into(),
            contract: Some(MeasurementContract {
                schema_version: 1,
                suite: suite.clone(),
                harness: tool.clone(),
                generator: tool.clone(),
                engine: Some(tool),
                build: None,
                validator_policy: "correctness-v1".into(),
                analysis_policy: "descriptive-v1".into(),
                profile,
            }),
            roles: [Role::Previous, Role::Candidate]
                .into_iter()
                .map(|role| {
                    (
                        role,
                        ArtifactRecord {
                            schema_version: 1,
                            id: "a".repeat(64),
                            file: FileIdentity {
                                bytes: if role == Role::Candidate { 7 } else { 10 },
                                ..file.clone()
                            },
                            build: None,
                        },
                    )
                })
                .collect(),
            inputs: vec![InputRecord {
                dataset: "tiny".into(),
                path: "/unused".into(),
                file,
            }],
            selected_cases: vec!["last-line".into()],
            host: None,
            tool_paths: None,
            experiment: None,
        };
        let case = suite
            .cases
            .first()
            .ok_or_else(|| BenchError::Evidence("fixture case".into()))?;
        let identity = SampleIdentity::new(
            &manifest.run_id,
            case,
            profile,
            &manifest.inputs,
            &"a".repeat(64),
        )?;
        let (timing, rss) = fixture_samples(case, profile, &identity);
        Ok((manifest, timing, rss))
    }
    fn fixture_samples(
        case: &crate::CaseSpec,
        profile: crate::MeasurementProfile,
        identity: &crate::SampleIdentity,
    ) -> (Vec<TimingSample>, Vec<RssSample>) {
        use crate::*;
        let mut timing = vec![];
        let mut rss = vec![];
        for role in [Role::Previous, Role::Candidate] {
            for batch in [TimingBatch::Forward, TimingBatch::Reverse] {
                for ordinal in 1..=profile.policy().samples_per_batch {
                    timing.push(TimingSample {
                        identity: identity.clone(),
                        case: case.id.clone(),
                        role,
                        batch,
                        ordinal,
                        seconds: if role == Role::Candidate { 1.0 } else { 2.0 },
                        status: 0,
                        raw_json: "raw/time.json".into(),
                        raw_stdout: "raw/stdout".into(),
                        raw_stderr: "raw/stderr".into(),
                    });
                }
            }
            for ordinal in 1..=profile.policy().rss_samples {
                rss.push(RssSample {
                    identity: identity.clone(),
                    case: case.id.clone(),
                    role,
                    ordinal,
                    rss: NativeRss {
                        platform: Platform::Linux,
                        value: u64::from(ordinal),
                        unit: RssUnit::Kibibytes,
                        bytes: u64::from(ordinal).saturating_mul(1024),
                        accounting_scope: "OS command peak".into(),
                    },
                    invocation_scope: "Direct".into(),
                    status: 0,
                    raw_resource: "raw/rss".into(),
                    raw_stdout: None,
                    raw_stderr: "raw/stderr".into(),
                    target_stderr: "raw/target".into(),
                });
            }
        }
        (timing, rss)
    }
    #[test]
    fn full_batches_rss_sizes_and_deterministic_order() -> TestResult {
        let (manifest, mut samples, rss) = fixture(crate::MeasurementProfile::Full)?;
        let first = analyze(&manifest, &samples, &rss)?;
        let case = first.cases.first().ok_or("case")?;
        let comparison = case.comparisons.first().ok_or("comparison")?;
        crate::test_support::equal(&(comparison.ratio), &(Some(0.5)))?;
        crate::test_support::equal(&(comparison.elapsed_change_percent), &(Some(-50.0)))?;
        crate::test_support::equal(&(comparison.size_change_bytes), &(-3))?;
        crate::test_support::equal(&(comparison.direction), &(Direction::Faster))?;
        crate::test_support::equal(&(comparison.scope), &("product comparison"))?;
        let candidate = case.roles.get(&Role::Candidate).ok_or("candidate")?;
        crate::test_support::equal(&(candidate.throughput), &(None))?;
        let memory = candidate.rss.as_ref().ok_or("rss")?;
        crate::test_support::equal(
            &(memory.median, memory.min, memory.max),
            &(3072.0, 1024.0, 5120.0),
        )?;
        samples.reverse();
        let second = analyze(&manifest, &samples, &rss)?;
        crate::test_support::equal(&(first.cases), &(second.cases))?;
        crate::test_support::equal(&(second.timing_samples), &(samples))?; // retain original observation order
        Ok(())
    }
    #[test]
    fn opposing_batches_are_inconclusive_and_smoke_suppresses_direction() -> TestResult {
        let (manifest, mut samples, rss) = fixture(crate::MeasurementProfile::Full)?;
        for sample in &mut samples {
            if sample.role == Role::Candidate && sample.batch == crate::TimingBatch::Reverse {
                sample.seconds = 4.0;
            }
        }
        crate::test_support::equal(
            &(analyze(&manifest, &samples, &rss)?
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .map(|c| c.direction)),
            &(Some(Direction::Inconclusive)),
        )?;
        let (manifest, samples, rss) = fixture(crate::MeasurementProfile::Smoke)?;
        crate::test_support::equal(
            &(analyze(&manifest, &samples, &rss)?
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .map(|c| c.direction)),
            &(Some(Direction::SmokeOnly)),
        )?;
        Ok(())
    }
    #[test]
    fn incomplete_duplicate_status_or_incompatible_samples_never_produce_ratios() -> TestResult {
        for fault in [
            "missing",
            "duplicate",
            "status",
            "scope",
            "run",
            "profile",
            "artifact",
            "zero",
        ] {
            let (manifest, mut samples, rss) = fixture(crate::MeasurementProfile::Full)?;
            if fault == "missing" {
                samples.pop();
            } else if fault == "duplicate" {
                samples.push(samples.first().ok_or("sample")?.clone());
            } else {
                let sample = samples.first_mut().ok_or("sample")?;
                match fault {
                    "status" => sample.status = 1,
                    "scope" => sample.identity.case_scope_id = "b".repeat(64),
                    "run" => sample.identity.run_id = "other".into(),
                    "profile" => sample.identity.profile = crate::MeasurementProfile::Smoke,
                    "artifact" => sample.identity.artifact_id = "b".repeat(64),
                    _ => sample.seconds = 0.0,
                }
            }
            crate::test_support::equal(
                &(analyze(&manifest, &samples, &rss)?
                    .cases
                    .first()
                    .and_then(|c| c.comparisons.first())
                    .and_then(|c| c.ratio)),
                &(None),
            )?;
        }
        Ok(())
    }
    #[test]
    fn dataset_io_and_work_changes_invalidate_saved_samples() -> TestResult {
        for fault in ["dataset", "io", "work"] {
            let (mut manifest, samples, rss) = fixture(crate::MeasurementProfile::Full)?;
            if fault == "dataset" {
                manifest.inputs.first_mut().ok_or("input")?.file.sha256 = "b".repeat(64);
            } else {
                let case = manifest
                    .contract
                    .as_mut()
                    .and_then(|c| c.suite.cases.first_mut())
                    .ok_or("case")?;
                if fault == "io" {
                    case.io.stdout = crate::StdoutPolicy::Discard {};
                } else {
                    case.work = Some(crate::Work {
                        amount: 10,
                        unit: crate::WorkUnit::Bytes,
                    });
                }
            }
            crate::test_support::equal(
                &(analyze(&manifest, &samples, &rss)?
                    .cases
                    .first()
                    .and_then(|c| c.comparisons.first())
                    .and_then(|c| c.ratio)),
                &(None),
            )?;
        }
        Ok(())
    }
    #[test]
    fn changed_build_policy_is_labelled_as_a_product_comparison() -> TestResult {
        let (mut manifest, samples, rss) = fixture(crate::MeasurementProfile::Full)?;
        let policy = crate::ResolvedBuildPolicy {
            compiler: "rustc fixture".into(),
            cargo: "cargo fixture".into(),
            target: "target".into(),
            settings: crate::BuildPolicy::default(),
            cargo_config_hashes: vec![],
            environment_hash: Some("a".repeat(64)),
        };
        for record in manifest.roles.values_mut() {
            record.build = Some(crate::BuildRecord {
                source_sha: "1".repeat(40),
                lockfile: record.file.clone(),
                policy: policy.clone(),
                command: vec!["cargo".into()],
                resolved_features: vec![],
                evidence: None,
            });
        }
        let source = analyze(&manifest, &samples, &rss)?;
        crate::test_support::equal(
            &source
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .map(|c| c.scope.as_str()),
            &Some("source-isolated comparison"),
        )?;
        manifest
            .roles
            .get_mut(&Role::Candidate)
            .and_then(|r| r.build.as_mut())
            .ok_or("build")?
            .policy
            .settings
            .strip = crate::StripPolicy::Symbols;
        let product = analyze(&manifest, &samples, &rss)?;
        crate::test_support::equal(
            &product
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .map(|c| c.scope.as_str()),
            &Some("product comparison"),
        )?;
        crate::test_support::equal(
            &product
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .and_then(|c| c.ratio),
            &Some(0.5),
        )?;
        Ok(())
    }
    #[test]
    fn unequal_dataset_operands_are_invalid_even_under_one_case_contract() -> TestResult {
        let (mut manifest, _, _) = fixture(crate::MeasurementProfile::Full)?;
        let case = manifest
            .contract
            .as_mut()
            .and_then(|c| c.suite.cases.first_mut())
            .ok_or("case")?;
        case.role_argv
            .insert(Role::Candidate, vec!["--different-work".into()]);
        crate::test_support::require(
            !case.equal_dataset_operands(
                crate::MeasurementProfile::Full,
                [Role::Previous, Role::Candidate],
            ),
            "unequal work accepted",
        )?;
        Ok(())
    }
    #[test]
    fn repeated_dataset_operands_suppress_offline_ratios_with_matching_sample_identities()
    -> TestResult {
        let (mut manifest, _, _) = fixture(crate::MeasurementProfile::Full)?;
        let case = manifest
            .contract
            .as_mut()
            .and_then(|contract| contract.suite.cases.first_mut())
            .ok_or("case")?;
        let mut doubled = case.argv.clone();
        doubled.push("@input:tiny".into());
        case.role_argv.insert(Role::Candidate, doubled);
        // Recompute identities for this exact saved contract so identity drift cannot
        // accidentally satisfy the regression before the unequal-work gate is checked.
        let identity = crate::SampleIdentity::new(
            &manifest.run_id,
            case,
            crate::MeasurementProfile::Full,
            &manifest.inputs,
            &"a".repeat(64),
        )?;
        let (samples, rss) = fixture_samples(case, crate::MeasurementProfile::Full, &identity);
        let analysis = analyze(&manifest, &samples, &rss)?;
        let case = analysis.cases.first().ok_or("analyzed case")?;
        let comparison = case.comparisons.first().ok_or("comparison")?;
        crate::test_support::equal(&comparison.ratio, &None)?;
        crate::test_support::require(
            comparison.batch_ratios.is_empty(),
            "unequal work exposed batch ratios",
        )?;
        crate::test_support::require(
            case.roles.values().all(|role| {
                role.issues
                    .iter()
                    .any(|issue| issue == "unequal dataset operands across roles")
            }),
            "unequal work was not diagnosed",
        )?;
        Ok(())
    }
    #[test]
    fn invalid_rss_sets_suppress_comparisons_but_zero_native_rss_is_valid() -> TestResult {
        let (manifest, samples, mut rss) = fixture(crate::MeasurementProfile::Full)?;
        rss.pop();
        let incomplete = analyze(&manifest, &samples, &rss)?;
        crate::test_support::equal(
            &incomplete
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .and_then(|c| c.ratio),
            &None,
        )?;
        let (manifest, samples, mut rss) = fixture(crate::MeasurementProfile::Full)?;
        for sample in &mut rss {
            sample.rss.value = 0;
            sample.rss.bytes = 0;
        }
        let zero = analyze(&manifest, &samples, &rss)?;
        crate::test_support::equal(
            &zero
                .cases
                .first()
                .and_then(|c| c.comparisons.first())
                .and_then(|c| c.ratio),
            &Some(0.5),
        )?;
        Ok(())
    }
    #[test]
    fn changed_role_artifact_cannot_reuse_samples() -> TestResult {
        let (mut manifest, samples, rss) = fixture(crate::MeasurementProfile::Full)?;
        manifest
            .roles
            .get_mut(&Role::Previous)
            .ok_or("previous")?
            .id = "b".repeat(64);
        let analysis = analyze(&manifest, &samples, &rss)?;
        let comparison = analysis
            .cases
            .first()
            .and_then(|c| c.comparisons.first())
            .ok_or("comparison")?;
        crate::test_support::equal(&comparison.ratio, &None)?;
        crate::test_support::require(
            comparison.batch_ratios.is_empty(),
            "invalid evidence exposed batch ratios",
        )?;
        Ok(())
    }
    #[test]
    fn selecting_reverse_roles_preserves_names_and_reverses_ratio() -> TestResult {
        let (manifest, samples, rss) = fixture(crate::MeasurementProfile::Full)?;
        let mut analysis = analyze(&manifest, &samples, &rss)?;
        select_comparison(
            &mut analysis,
            &manifest,
            ComparisonSelection {
                baseline: Role::Candidate,
                candidate: Role::Previous,
            },
        )?;
        let comparison = analysis
            .cases
            .first()
            .and_then(|c| c.comparisons.first())
            .ok_or("comparison")?;
        crate::test_support::equal(
            &(
                comparison.baseline,
                comparison.candidate,
                comparison.ratio,
                comparison.direction,
            ),
            &(
                Role::Candidate,
                Role::Previous,
                Some(2.0),
                Direction::Slower,
            ),
        )?;
        crate::test_support::equal(&analysis.timing_samples, &samples)?;
        Ok(())
    }
    #[test]
    fn selected_reference_is_product_scope_and_smoke_never_has_direction() -> TestResult {
        let (mut manifest, mut samples, mut rss) = fixture(crate::MeasurementProfile::Smoke)?;
        let record = manifest.roles.remove(&Role::Previous).ok_or("previous")?;
        manifest.roles.insert(Role::Reference, record);
        for sample in &mut samples {
            if sample.role == Role::Previous {
                sample.role = Role::Reference;
            }
        }
        for sample in &mut rss {
            if sample.role == Role::Previous {
                sample.role = Role::Reference;
            }
        }
        let mut analysis = analyze(&manifest, &samples, &rss)?;
        select_comparison(
            &mut analysis,
            &manifest,
            ComparisonSelection {
                baseline: Role::Candidate,
                candidate: Role::Reference,
            },
        )?;
        let comparison = analysis
            .cases
            .first()
            .and_then(|c| c.comparisons.first())
            .ok_or("comparison")?;
        crate::test_support::equal(&comparison.scope, &"product comparison")?;
        crate::test_support::equal(&comparison.direction, &Direction::SmokeOnly)?;
        crate::test_support::require(
            select_comparison(
                &mut analysis,
                &manifest,
                ComparisonSelection {
                    baseline: Role::Candidate,
                    candidate: Role::Candidate,
                },
            )
            .is_err(),
            "same-role comparison accepted",
        )?;
        Ok(())
    }
}
