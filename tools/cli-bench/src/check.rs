use crate::{
    BenchError, CapturePaths, CaseId, CaseSpec, CommandOutput, ComparisonTarget, CorrectnessRule,
    DatasetPreparation, DatasetSet, FileIdentity, HostValue, MeasurementProfile, OwnedScratch,
    ProcessRunner, Role, RoleBindings, RoleRequest, RunEvent, RunWriter, Store, Stream, Suite,
    TailUnit,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

/// Explicit suite/profile/case selection and role/build resources.
#[derive(Debug)]
pub struct ExperimentPreparation<'a> {
    pub run: &'a RoleRequest,
    pub suite: &'a Suite,
    pub profile: MeasurementProfile,
    /// Empty selects every declared case, otherwise unique declared identifiers.
    pub selected_cases: &'a [CaseId],
    pub expected_datasets: Option<&'a DatasetSet>,
}
/// Prepared resources; only validation can produce a measurement capability.
#[derive(Debug)]
pub struct PreparedExperiment<'lock> {
    measurement_lock: &'lock crate::MeasurementLock,
    suite: Suite,
    profile: MeasurementProfile,
    cases: Vec<CaseSpec>,
    bindings: RoleBindings,
    datasets: DatasetSet,
    scratch: BTreeMap<CaseId, OwnedScratch>,
}
/// Opaque successful correctness gate. No public constructor or deserialization.
#[derive(Debug)]
pub struct ValidatedExperiment<'lock> {
    prepared: PreparedExperiment<'lock>,
    report: ValidationReport,
    evidence_budget: crate::budget::EvidenceBudget,
}
/// One exact check, including failures; no selected comparison is silently omitted.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationFinding {
    pub case: Option<String>,
    pub role: Option<Role>,
    pub check: String,
    pub passed: bool,
    pub detail: Option<String>,
}
/// Captured native exit/stop outcomes and byte identities, retained before comparison.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationObservation {
    pub case: String,
    pub role: Role,
    pub boundary: String,
    pub status: String,
    pub stopped: Option<String>,
    pub stdout: Option<FileIdentity>,
    pub stderr: FileIdentity,
    /// Actual directory modes; the v1 directory rule deliberately excludes timestamps.
    pub directories: BTreeMap<String, u32>,
}
/// Complete preflight gate, written even when correctness fails.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationReport {
    pub schema_version: u32,
    pub profile: MeasurementProfile,
    pub selected_cases: Vec<String>,
    pub inherited_umask: HostValue,
    pub findings: Vec<ValidationFinding>,
    pub observations: Vec<ValidationObservation>,
}
impl ValidationReport {
    #[must_use]
    pub fn passed(&self) -> bool {
        self.findings.iter().all(|finding| finding.passed)
    }
    fn record(
        &mut self,
        case: Option<&str>,
        role: Option<Role>,
        check: impl Into<String>,
        result: Result<(), BenchError>,
    ) {
        self.findings.push(ValidationFinding {
            case: case.map(str::to_owned),
            role,
            check: check.into(),
            passed: result.is_ok(),
            detail: result.err().map(|error| error.to_string()),
        });
    }
}
impl PreparedExperiment<'_> {
    #[must_use]
    pub const fn suite(&self) -> &Suite {
        &self.suite
    }
    #[must_use]
    pub const fn measurement_lock(&self) -> &crate::MeasurementLock {
        self.measurement_lock
    }

    #[must_use]
    pub const fn roles(&self) -> &RoleBindings {
        &self.bindings
    }
    #[must_use]
    pub const fn datasets(&self) -> &DatasetSet {
        &self.datasets
    }
    #[must_use]
    pub fn cases(&self) -> &[CaseSpec] {
        &self.cases
    }
    pub(crate) fn sample_identity(
        &self,
        run_id: &str,
        case: &CaseSpec,
        role: Role,
    ) -> Result<crate::SampleIdentity, BenchError> {
        let artifact = &self
            .bindings
            .roles
            .get(&role)
            .ok_or_else(|| failure("unbound sample role"))?
            .artifact;
        crate::SampleIdentity::new(
            run_id,
            case,
            self.profile,
            &self.datasets.inputs.values().cloned().collect::<Vec<_>>(),
            &artifact.id,
        )
    }
    #[must_use]
    pub const fn profile(&self) -> MeasurementProfile {
        self.profile
    }
    #[must_use]
    pub fn scratch(&self, case: &CaseId) -> Option<&OwnedScratch> {
        self.scratch.get(case)
    }
    fn revalidate(&self) -> Result<(), BenchError> {
        crate::verify_datasets(&self.datasets)?;
        for executable in self
            .bindings
            .roles
            .values()
            .chain(self.bindings.generator.iter())
        {
            crate::verify_file(&executable.path, &executable.artifact.file)?;
        }
        if let Some(pipeline) = &self.bindings.pipeline {
            for tool in [&pipeline.bash, &pipeline.cat] {
                crate::verify_file(&tool.path, &tool.identity.file)?;
            }
        }
        for scratch in self.scratch.values() {
            scratch.verify()?;
        }
        Ok(())
    }
}
impl<'lock> ValidatedExperiment<'lock> {
    #[must_use]
    pub const fn prepared(&self) -> &PreparedExperiment<'lock> {
        &self.prepared
    }
    #[must_use]
    pub const fn roles(&self) -> &RoleBindings {
        self.prepared.roles()
    }
    #[must_use]
    pub const fn datasets(&self) -> &DatasetSet {
        self.prepared.datasets()
    }
    #[must_use]
    pub const fn report(&self) -> &ValidationReport {
        &self.report
    }
    /// Final read-only identity check; never refreshes expected input identities.
    /// # Errors
    /// Rejects changed datasets, executable/tool bytes or scratch ownership.
    pub fn revalidate(&self) -> Result<(), BenchError> {
        self.prepared.revalidate()
    }
    /// Repeat the selected case's exact correctness checks after measurement.
    /// Retains a fresh report and rechecks identities without refreshing expectations.
    /// A plain caller runner inherits the validation writer's evidence budget;
    /// enclosing measurement stages keep their explicitly bound writer budget.
    /// # Errors
    /// Rejects changed identities, output/status/effect drift or evidence failures.
    pub fn final_case_check(
        &self,
        case: &CaseId,
        evidence: &Path,
        runner: &ProcessRunner,
    ) -> Result<(), BenchError> {
        let evidence_runner = runner.with_default_evidence(&self.evidence_budget);
        let runner = &evidence_runner;
        let spec = self
            .prepared
            .cases
            .iter()
            .find(|spec| spec.id == case.as_str())
            .ok_or_else(|| failure("unknown selected case"))?;
        fs::create_dir(evidence)?;
        let mut report = ValidationReport {
            schema_version: 1,
            profile: self.prepared.profile,
            selected_cases: vec![case.as_str().into()],
            inherited_umask: self.report.inherited_umask.clone(),
            findings: vec![],
            observations: vec![],
        };
        report.record(None, None, "pre-final-identities", self.revalidate());
        if report.passed() {
            let result = validate_case(&self.prepared, spec, evidence, runner, &mut report);
            report.record(Some(case.as_str()), None, "final-execution", result);
        }
        report.record(None, None, "final-identities", self.revalidate());
        report.record(
            None,
            None,
            "inherited-umask",
            same(
                &report.inherited_umask,
                &crate::host::inherited_umask(),
                "inherited umask observation changed",
            ),
        );
        runner.write_json(&evidence.join("report.json"), &report)?;
        if !report.passed() {
            return Err(failure(
                "final correctness gate failed; see retained final report",
            ));
        }
        Ok(())
    }
    /// Verify declared directory effects against this role's successful gate.
    /// Call after later warmup/sample invocations, outside their timing boundary.
    /// # Errors
    /// Rejects missing cases/roles, ownership changes and wrong directory types/paths/modes.
    pub fn verify_effects(&self, case: &CaseId, role: Role) -> Result<(), BenchError> {
        let spec = self
            .prepared
            .cases
            .iter()
            .find(|spec| spec.id == case.as_str())
            .ok_or_else(|| failure("unknown selected case"))?;
        let scratch = self
            .prepared
            .scratch(case)
            .ok_or_else(|| failure("missing scratch"))?;
        scratch.verify()?;
        let observation = self
            .report
            .observations
            .iter()
            .find(|observation| {
                observation.case == case.as_str()
                    && observation.role == role
                    && observation.boundary == "capture"
            })
            .ok_or_else(|| failure("unvalidated role"))?;
        let rules = rules(spec, self.prepared.profile);
        for rule in rules {
            if let CorrectnessRule::DirectoryTree {
                paths,
                compare_mode_to,
            } = rule
            {
                let actual = directory_effect(scratch.path(), paths)?;
                if compare_mode_to.is_some() && actual != observation.directories {
                    return Err(failure("directory modes changed after invocation"));
                }
            }
        }
        Ok(())
    }
}
/// Prepare all selected resources outside timing. Scratch lives under the explicit
/// build cache root, while generated data and validation evidence live in Store.
/// # Errors
/// Returns invalid selection, build, input or scratch failures.
pub fn prepare_experiment<'lock>(
    measurement_lock: &'lock crate::MeasurementLock,
    request: &ExperimentPreparation<'_>,
    store: &Store,
    runner: &ProcessRunner,
) -> Result<PreparedExperiment<'lock>, BenchError> {
    crate::validate_suite(request.suite)?;
    let selected: BTreeSet<_> = request.selected_cases.iter().map(CaseId::as_str).collect();
    if selected.len() != request.selected_cases.len()
        || selected
            .iter()
            .any(|id| !request.suite.cases.iter().any(|case| case.id == *id))
    {
        return Err(BenchError::invalid(
            "case selection contains duplicate or unknown IDs",
        ));
    }
    let cases: Vec<_> = request
        .suite
        .cases
        .iter()
        .filter(|case| selected.is_empty() || selected.contains(case.id.as_str()))
        .cloned()
        .collect();
    let bindings = crate::bind_roles(measurement_lock, request.run, request.suite, store, runner)?;
    let datasets = crate::prepare_datasets(
        measurement_lock,
        request.suite,
        &DatasetPreparation {
            profile: request.profile,
            bindings: &bindings,
            expected: request.expected_datasets,
        },
        store,
        runner,
    )?;
    crate::process::utf8_path(&request.run.cache_root)?;
    let ancestor = request
        .run
        .cache_root
        .ancestors()
        .find(|path| path.symlink_metadata().is_ok())
        .ok_or_else(|| failure("cache root has no existing ancestor"))?;
    crate::sandbox::real_directory(ancestor)?;
    fs::create_dir_all(&request.run.cache_root)?;
    let mut scratch = BTreeMap::new();
    for case in &cases {
        let id = CaseId::new(case.id.clone())?;
        let owned = crate::create_scratch(&request.run.cache_root, &id)?;
        crate::reset_case(case, &datasets, &owned)?;
        scratch.insert(id, owned);
    }
    Ok(PreparedExperiment {
        measurement_lock,
        suite: request.suite.clone(),
        profile: request.profile,
        cases,
        bindings,
        datasets,
        scratch,
    })
}
#[derive(Debug)]
struct Captured {
    stdout: PathBuf,
    stderr: PathBuf,
    directories: BTreeMap<String, u32>,
}
/// Check every selected role/case and persist the complete gate in validation/report.json.
///
/// Captures exact bytes first, then separately checks the declared output boundary.
/// All child executions inherit the same process umask; unavailable readings stay explicit.
/// # Errors
/// Returns failure instead of a validation capability when any check fails.
pub fn validate_experiment<'lock>(
    prepared: PreparedExperiment<'lock>,
    writer: &mut RunWriter,
    runner: &ProcessRunner,
) -> Result<ValidatedExperiment<'lock>, BenchError> {
    let evidence_runner = runner.with_evidence(&writer.budget);
    let runner = &evidence_runner;
    if !writer.matches_suite(&prepared.suite) {
        return Err(failure("run writer suite differs from prepared suite"));
    }
    runner.write_json(
        &writer.path().join("measurement-lock.json"),
        &serde_json::json!({"wait_seconds": prepared.measurement_lock.wait_duration().as_secs_f64()}),
    )?;
    let evidence = writer.path().join("validation");
    fs::create_dir(&evidence)?;
    let mut report = ValidationReport {
        schema_version: 1,
        profile: prepared.profile,
        selected_cases: prepared.cases.iter().map(|case| case.id.clone()).collect(),
        inherited_umask: crate::host::inherited_umask(),
        findings: vec![],
        observations: vec![],
    };
    for case in &prepared.cases {
        validate_case(&prepared, case, &evidence, runner, &mut report)?;
    }
    report.record(None, None, "final-input-identities", prepared.revalidate());
    let final_umask = crate::host::inherited_umask();
    report.record(
        None,
        None,
        "inherited-umask",
        same(
            &report.inherited_umask,
            &final_umask,
            "inherited umask observation changed",
        ),
    );
    runner.write_json(&evidence.join("report.json"), &report)?;
    writer.append_event(&if report.passed() {
        RunEvent::Stage("correctness gate passed".into())
    } else {
        RunEvent::Failure("correctness gate failed; see validation/report.json".into())
    })?;
    if !report.passed() {
        return Err(failure(
            "correctness gate failed; complete findings retained in validation/report.json",
        ));
    }
    Ok(ValidatedExperiment {
        prepared,
        report,
        evidence_budget: writer.budget.clone(),
    })
}
fn validate_case(
    prepared: &PreparedExperiment<'_>,
    case: &CaseSpec,
    evidence: &Path,
    runner: &ProcessRunner,
    report: &mut ValidationReport,
) -> Result<(), BenchError> {
    let id = CaseId::new(case.id.clone())?;
    let scratch = prepared
        .scratch(&id)
        .ok_or_else(|| failure("missing prepared scratch"))?;
    let directory = evidence.join(&case.id);
    fs::create_dir(&directory)?;
    let mut captured = BTreeMap::new();
    for role in prepared.bindings.roles.keys() {
        let result = execute(prepared, case, *role, scratch, &directory, runner, report);
        match result {
            Ok(value) => {
                captured.insert(*role, value);
            }
            Err(error) => report.record(Some(&case.id), Some(*role), "execution", Err(error)),
        }
    }
    for role in prepared.bindings.roles.keys() {
        for (index, rule) in rules(case, prepared.profile).iter().enumerate() {
            check_rule(
                rule,
                (*role, &case.id, index),
                captured.get(role),
                &captured,
                &prepared.bindings.roles,
                &prepared.datasets,
                report,
            );
        }
    }
    Ok(())
}
fn execute(
    prepared: &PreparedExperiment<'_>,
    case: &CaseSpec,
    role: Role,
    scratch: &OwnedScratch,
    directory: &Path,
    runner: &ProcessRunner,
    report: &mut ValidationReport,
) -> Result<Captured, BenchError> {
    let mut captured = None;
    for boundary in ["capture", "declared-sink"] {
        crate::reset_case(case, &prepared.datasets, scratch)?;
        let mut invocation = crate::resolve_invocation(
            case,
            role,
            prepared.profile,
            &prepared.bindings,
            &prepared.datasets,
            scratch.path(),
        )?;
        if boundary == "capture" {
            invocation.command.stdout = CommandOutput::Capture;
        }
        let paths = CapturePaths {
            stdout: directory.join(format!("{role:?}-{boundary}.stdout")),
            stderr: directory.join(format!("{role:?}-{boundary}.stderr")),
        };
        let outcome = runner.execute(&invocation.command, &paths)?;
        report.record(
            Some(&case.id),
            Some(role),
            format!("{boundary}:status"),
            outcome.check_expected(case.expected_status),
        );
        let output = match &invocation.command.stdout {
            CommandOutput::Capture => Some(paths.stdout.clone()),
            CommandOutput::File(path) => Some(path.clone()),
            _ => None,
        };
        let directories =
            observed_directories(case, prepared.profile, scratch, role, boundary, report);
        report.observations.push(ValidationObservation {
            case: case.id.clone(),
            role,
            boundary: boundary.into(),
            status: format!("{:?}", outcome.status),
            stopped: outcome.stopped.map(|value| format!("{value:?}")),
            stdout: output
                .as_ref()
                .map(|path| crate::fingerprint(path))
                .transpose()?,
            stderr: crate::fingerprint(&paths.stderr)?,
            directories: directories.clone(),
        });
        if let Some(first) = &captured {
            let first: &Captured = first;
            report.record(
                Some(&case.id),
                Some(role),
                "declared-sink:stderr",
                equal_files(&first.stderr, &paths.stderr),
            );
            if let Some(output) = &output {
                report.record(
                    Some(&case.id),
                    Some(role),
                    "declared-sink:stdout",
                    equal_files(&first.stdout, output),
                );
            }
            if rules(case, prepared.profile).iter().any(|rule| {
                matches!(
                    rule,
                    CorrectnessRule::DirectoryTree {
                        compare_mode_to: Some(_),
                        ..
                    }
                )
            }) {
                report.record(
                    Some(&case.id),
                    Some(role),
                    "declared-sink:effects",
                    same(
                        &first.directories,
                        &directories,
                        "declared output boundary changed directory effects",
                    ),
                );
            }
        } else {
            captured = Some(Captured {
                stdout: paths.stdout,
                stderr: paths.stderr,
                directories,
            });
        }
    }
    captured.ok_or_else(|| failure("capture unavailable"))
}
fn observed_directories(
    case: &CaseSpec,
    profile: MeasurementProfile,
    scratch: &OwnedScratch,
    active: Role,
    boundary: &str,
    report: &mut ValidationReport,
) -> BTreeMap<String, u32> {
    let mut directories = BTreeMap::new();
    for rule in rules(case, profile) {
        if let CorrectnessRule::DirectoryTree { paths, .. } = rule {
            let effect = directory_effect(scratch.path(), paths);
            if let Ok(value) = &effect {
                directories.clone_from(value);
            }
            report.record(
                Some(&case.id),
                Some(active),
                format!("{boundary}:directory-tree"),
                effect.map(|_| ()),
            );
        }
    }
    directories
}

fn rules(case: &CaseSpec, profile: MeasurementProfile) -> &[CorrectnessRule] {
    case.profiles
        .get(&profile)
        .and_then(|value| value.correctness.as_deref())
        .unwrap_or(&case.correctness)
}
fn targets(
    target: ComparisonTarget,
    selected: &BTreeMap<Role, crate::BoundExecutable>,
) -> Vec<Role> {
    match target {
        ComparisonTarget::SelectedBaselines => selected
            .keys()
            .copied()
            .filter(|role| *role != Role::Candidate)
            .collect(),
        ComparisonTarget::Reference => vec![Role::Reference],
        ComparisonTarget::Previous => vec![Role::Previous],
        ComparisonTarget::Candidate => vec![Role::Candidate],
    }
}

fn check_rule(
    rule: &CorrectnessRule,
    context: (Role, &str, usize),
    actual: Option<&Captured>,
    captured: &BTreeMap<Role, Captured>,
    selected: &BTreeMap<Role, crate::BoundExecutable>,
    datasets: &DatasetSet,
    report: &mut ValidationReport,
) {
    let (active, case, index) = context;
    let target = match rule {
        CorrectnessRule::Comparator { target, .. } => Some(*target),
        CorrectnessRule::DirectoryTree {
            compare_mode_to, ..
        } => *compare_mode_to,
        _ => None,
    };
    if let Some(target) = target {
        for other in targets(target, selected) {
            let result = actual
                .ok_or_else(|| failure("role capture unavailable"))
                .and_then(|actual| {
                    captured
                        .get(&other)
                        .ok_or_else(|| {
                            failure(format!("required comparator {other:?} unavailable"))
                        })
                        .and_then(|expected| match rule {
                            CorrectnessRule::Comparator { stream, .. } => equal_files(
                                stream_path(actual, *stream),
                                stream_path(expected, *stream),
                            ),
                            _ => same(
                                &actual.directories,
                                &expected.directories,
                                "directory modes differ from comparator",
                            ),
                        })
                });
            report.record(
                Some(case),
                Some(active),
                format!("rule-{index}:{other:?}"),
                result,
            );
        }
    } else {
        report.record(
            Some(case),
            Some(active),
            format!("rule-{index}"),
            actual
                .ok_or_else(|| failure("role capture unavailable"))
                .and_then(|actual| independent(rule, actual, datasets)),
        );
    }
}
fn independent(
    rule: &CorrectnessRule,
    actual: &Captured,
    datasets: &DatasetSet,
) -> Result<(), BenchError> {
    match rule {
        CorrectnessRule::Literal { stream, text } => {
            equal_readers(File::open(stream_path(actual, *stream))?, text.as_bytes())
        }
        CorrectnessRule::Hex { stream, hex } => equal_readers(
            File::open(stream_path(actual, *stream))?,
            decode_hex(hex)?.as_slice(),
        ),
        CorrectnessRule::EmptyStderr {} => equal_readers(File::open(&actual.stderr)?, &b""[..]),
        CorrectnessRule::BytePattern { .. }
        | CorrectnessRule::TextShape { .. }
        | CorrectnessRule::Records { .. } => {
            crate::dataset::validate_shape(File::open(&actual.stdout)?, rule).map(|_| ())
        }
        CorrectnessRule::TailSlice {
            dataset,
            unit,
            count,
            stream,
        } => {
            let input = datasets
                .inputs
                .get(dataset)
                .ok_or_else(|| failure("missing tail input"))?;
            let mut file = File::open(&input.path)?;
            let start = tail_start(&mut file, *unit, *count)?;
            file.seek(SeekFrom::Start(start))?;
            equal_readers(file, File::open(stream_path(actual, *stream))?)
        }
        CorrectnessRule::DirectoryTree { .. } => Ok(()),
        CorrectnessRule::Comparator { .. } => Err(failure("unresolved comparator")),
    }
}
fn stream_path(capture: &Captured, stream: Stream) -> &Path {
    match stream {
        Stream::Stdout => &capture.stdout,
        Stream::Stderr => &capture.stderr,
    }
}
fn decode_hex(hex: &str) -> Result<Vec<u8>, BenchError> {
    hex.as_bytes()
        .chunks(2)
        .map(|pair| {
            let text = std::str::from_utf8(pair).map_err(|error| failure(error.to_string()))?;
            u8::from_str_radix(text, 16).map_err(|error| failure(error.to_string()))
        })
        .collect()
}
fn equal_files(a: &Path, b: &Path) -> Result<(), BenchError> {
    equal_readers(File::open(a)?, File::open(b)?)
}
fn equal_readers(mut a: impl Read, mut b: impl Read) -> Result<(), BenchError> {
    let mut left = [0_u8; 8192];
    let mut right = [0_u8; 8192];
    loop {
        let size = a.read(&mut left)?;
        let bytes = left
            .get(..size)
            .ok_or_else(|| failure("invalid read length"))?;
        if size == 0 {
            return if b.read(
                right
                    .get_mut(..1)
                    .ok_or_else(|| failure("invalid probe length"))?,
            )? == 0
            {
                Ok(())
            } else {
                Err(failure("stream bytes differ"))
            };
        }
        let target = right
            .get_mut(..size)
            .ok_or_else(|| failure("invalid read length"))?;
        match b.read_exact(target) {
            Ok(()) if bytes == target => {}
            Ok(()) => return Err(failure("stream bytes differ")),
            Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => {
                return Err(failure("stream lengths differ"));
            }
            Err(error) => return Err(error.into()),
        }
    }
}
fn same<T: PartialEq>(actual: &T, expected: &T, message: &str) -> Result<(), BenchError> {
    if actual == expected {
        Ok(())
    } else {
        Err(failure(message))
    }
}
fn directory_effect(root: &Path, expected: &[String]) -> Result<BTreeMap<String, u32>, BenchError> {
    let entries = crate::sandbox::entries(root)?;
    let paths: BTreeSet<_> = expected.iter().map(String::as_str).collect();
    if entries.values().any(|entry| !entry.directory)
        || entries.keys().map(String::as_str).collect::<BTreeSet<_>>() != paths
    {
        return Err(failure(
            "directory types/paths differ from exact declared tree",
        ));
    }
    Ok(entries
        .into_iter()
        .map(|(path, entry)| (path, entry.mode))
        .collect())
}
fn tail_start(file: &mut File, unit: TailUnit, count: u64) -> Result<u64, BenchError> {
    let size = file.metadata()?.len();
    if count == 0 {
        return Ok(size);
    }
    if unit == TailUnit::Bytes {
        return Ok(size.saturating_sub(count));
    }
    let mut remaining = count;
    let mut position = size;
    let mut buffer = [0_u8; 8192];
    while position > 0 {
        let start = position.saturating_sub(8192);
        let length = usize::try_from(position.saturating_sub(start))
            .map_err(|error| failure(error.to_string()))?;
        let bytes = buffer
            .get_mut(..length)
            .ok_or_else(|| failure("invalid tail chunk"))?;
        file.seek(SeekFrom::Start(start))?;
        file.read_exact(bytes)?;
        for (index, byte) in bytes.iter().enumerate().rev() {
            let offset = start
                .checked_add(u64::try_from(index).map_err(|error| failure(error.to_string()))?)
                .ok_or_else(|| failure("tail offset overflow"))?;
            if *byte == b'\n' && offset != size.saturating_sub(1) {
                remaining = remaining.saturating_sub(1);
                if remaining == 0 {
                    return offset
                        .checked_add(1)
                        .ok_or_else(|| failure("tail offset overflow"));
                }
            }
        }
        position = start;
    }
    Ok(0)
}
fn failure(message: impl Into<String>) -> BenchError {
    BenchError::Evidence(message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{
        gate_support::{failed_gate_never_times, fake_timer},
        validation_support,
    };
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    fn require(value: bool, message: &str) -> TestResult {
        if value { Ok(()) } else { Err(message.into()) }
    }
    #[test]
    fn public_final_checks_retain_the_writer_budget_with_a_plain_runner() -> TestResult {
        let mut fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        fixture.suite.limits.max_evidence_bytes = 65_536;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Full)?,
            &mut writer,
            &fixture.runner,
        )?;
        // Leave less than the two successful five-byte correctness captures need.
        // This is retained evidence, not a changed execution policy or fixture timeout.
        let remaining = writer.budget.remaining()?;
        let padding = remaining
            .checked_sub(8)
            .ok_or("fixture needs room before final check")?;
        fs::write(
            writer.path().join("retained-padding"),
            vec![0; usize::try_from(padding)?],
        )?;
        let final_path = writer.path().join("direct-final");
        require(
            validated
                .final_case_check(&CaseId::new("last-line")?, &final_path, &fixture.runner)
                .is_err(),
            "plain runner bypassed final-check quota",
        )?;
        let files = crate::store::inventory(&final_path)?;
        let captured: u64 = files
            .iter()
            .filter(|(name, _)| name.ends_with(".stdout") || name.ends_with(".stderr"))
            .map(|(_, identity)| identity.bytes)
            .sum();
        require(
            captured <= 8,
            "final check wrote captures beyond shared remaining allowance",
        )?;
        require(
            fs::read_to_string(final_path.join("report.json"))?.contains("EvidenceLimit"),
            "final cap failure lost observations",
        )?;
        let later = writer.path().join("later-final");
        require(
            validated
                .final_case_check(&CaseId::new("last-line")?, &later, &fixture.runner)
                .is_err(),
            "final check started more children after exhaustion",
        )?;
        require(
            !crate::store::inventory(&later)?
                .keys()
                .any(|name| name.ends_with(".stdout") || name.ends_with(".stderr")),
            "later final check created child captures",
        )?;
        let bundle = writer.record_failure(
            crate::RunOutcome::Failed,
            "final check reached evidence cap",
        )?;
        fixture.store.load_run(&bundle.manifest.run_id)?;
        let total = crate::budget::size(&bundle.path)?;
        let index = fs::metadata(bundle.path.join("checksums.json"))?.len();
        require(
            total
                <= fixture.suite.limits.max_evidence_bytes
                    + crate::budget::FAILURE_METADATA_BYTES
                    + index,
            "final checks escaped failure metadata allowance",
        )?;
        Ok(())
    }
    #[test]
    fn exact_assertions_preserve_nul_invalid_utf8_whitespace_and_shape() -> TestResult {
        let temp = assert_fs::TempDir::new()?;
        let actual = Captured {
            stdout: temp.path().join("stdout"),
            stderr: temp.path().join("stderr"),
            directories: BTreeMap::new(),
        };
        fs::write(&actual.stdout, b"\0\xff\0\xff")?;
        fs::write(&actual.stderr, b"x \n")?;
        for rule in [
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex: "00ff00ff".into(),
            },
            CorrectnessRule::BytePattern {
                bytes: 4,
                pattern_hex: "00ff".into(),
            },
            CorrectnessRule::Literal {
                stream: Stream::Stderr,
                text: "x \n".into(),
            },
        ] {
            independent(&rule, &actual, &DatasetSet::default())?;
        }
        for rule in [
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex: "00ff".into(),
            },
            CorrectnessRule::Literal {
                stream: Stream::Stderr,
                text: "x\n".into(),
            },
            CorrectnessRule::EmptyStderr {},
        ] {
            require(
                independent(&rule, &actual, &DatasetSet::default()).is_err(),
                "byte mismatch accepted",
            )?;
        }
        fs::write(&actual.stdout, b"aa bb\ncc dd\n")?;
        independent(
            &CorrectnessRule::TextShape {
                records: 2,
                words_per_record: 2,
                word_length: 2,
            },
            &actual,
            &DatasetSet::default(),
        )?;
        fs::write(&actual.stdout, b"a\na\nb\nb\n")?;
        independent(
            &CorrectnessRule::Records {
                records: vec!["a".into(), "b".into()],
                repeat: 2,
                cycles: 1,
            },
            &actual,
            &DatasetSet::default(),
        )?;
        Ok(())
    }
    #[test]
    fn independent_tail_handles_empty_unterminated_and_chunk_boundary_lines() -> TestResult {
        let temp = assert_fs::TempDir::new()?;
        let path = temp.path().join("input");
        for (bytes, count, expected) in [
            (&b""[..], 1, &b""[..]),
            (b"a\nb\n", 1, b"b\n"),
            (b"a\nb", 1, b"b"),
            (b"a\nb\n", 2, b"a\nb\n"),
            (b"a\nb\n", 0, b""),
            (b"\n\n", 1, b"\n"),
        ] {
            fs::write(&path, bytes)?;
            let mut file = File::open(&path)?;
            let offset = tail_start(&mut file, TailUnit::Lines, count)?;
            file.seek(SeekFrom::Start(offset))?;
            equal_readers(file, expected)?;
        }
        fs::write(&path, [vec![b'a'; 8200], b"\ntail\0\xff".to_vec()].concat())?;
        let mut file = File::open(&path)?;
        let offset = tail_start(&mut file, TailUnit::Lines, 1)?;
        file.seek(SeekFrom::Start(offset))?;
        equal_readers(file, &b"tail\0\xff"[..])?;
        let mut file = File::open(&path)?;
        let offset = tail_start(&mut file, TailUnit::Bytes, 2)?;
        file.seek(SeekFrom::Start(offset))?;
        equal_readers(file, &b"\0\xff"[..])?;
        Ok(())
    }

    #[test]
    fn unexpected_status_prevents_all_timing() -> validation_support::TestResult {
        failed_gate_never_times(&crate::test_support::validation_fixture(
            "printf 'EFGH\\n'; exit 7",
            "printf 'EFGH\\n'",
        )?)
    }

    #[test]
    fn stderr_mismatch_is_not_trimmed() -> validation_support::TestResult {
        let mut fixture = crate::test_support::validation_fixture(
            "printf 'EFGH\\n'; printf 'note \\n' >&2",
            "printf 'EFGH\\n'; printf 'note\\n' >&2",
        )?;
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.correctness
            .retain(|rule| !matches!(rule, crate::CorrectnessRule::EmptyStderr {}));
        case.correctness.push(crate::CorrectnessRule::Comparator {
            target: crate::ComparisonTarget::SelectedBaselines,
            stream: crate::Stream::Stderr,
        });
        failed_gate_never_times(&fixture)
    }

    #[test]
    fn independent_oracle_detects_shared_bug() -> validation_support::TestResult {
        failed_gate_never_times(&crate::test_support::validation_fixture(
            "printf 'WRONG\\n'",
            "printf 'WRONG\\n'",
        )?)
    }

    #[test]
    fn missing_reference_is_not_skipped() -> validation_support::TestResult {
        let mut fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        fixture
            .suite
            .cases
            .first_mut()
            .ok_or("case")?
            .correctness
            .push(crate::CorrectnessRule::Comparator {
                target: crate::ComparisonTarget::Reference,
                stream: crate::Stream::Stdout,
            });
        failed_gate_never_times(&fixture)
    }

    #[test]
    fn final_input_tamper_prevents_all_timing() -> validation_support::TestResult {
        use crate::*;
        let mut fixture = crate::test_support::validation_fixture(
            "printf 'EFGH\\n'; if [ ! -p /dev/stdout ]; then printf 'WXYZ\\nIJKL\\n' > \"$1\"; fi",
            "printf 'EFGH\\n'",
        )?;
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.argv = vec!["@input:tiny".into()];
        case.io.stdout = StdoutPolicy::Discard {};
        case.correctness = vec![
            CorrectnessRule::Literal {
                stream: Stream::Stdout,
                text: "EFGH\n".into(),
            },
            CorrectnessRule::EmptyStderr {},
        ];
        failed_gate_never_times(&fixture)
    }

    #[test]
    fn nonzero_expected_status_is_a_value_and_all_findings_are_retained()
    -> validation_support::TestResult {
        use crate::*;
        let mut fixture = crate::test_support::validation_fixture(
            "printf 'EFGH\\n'; exit 3",
            "printf 'EFGH\\n'; exit 3",
        )?;
        fixture
            .suite
            .cases
            .first_mut()
            .ok_or("case")?
            .expected_status = 3;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(crate::MeasurementProfile::Full)?,
            &mut writer,
            &fixture.runner,
        )?;
        require(validated.report().passed(), "expected nonzero rejected")?;
        fake_timer(validated, &fixture.root.path().join("timed"))?;
        require(
            fixture.root.path().join("timed").is_file(),
            "successful capability never reached timer",
        )?;
        drop(writer);
        drop(fixture);
        let fixture = crate::test_support::validation_fixture(
            "printf wrong; printf problem >&2; exit 7",
            "printf shared-bug; exit 9",
        )?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        require(
            validate_experiment(
                fixture.prepare(crate::MeasurementProfile::Full)?,
                &mut writer,
                &fixture.runner,
            )
            .is_err(),
            "bad gate passed",
        )?;
        let report: ValidationReport = serde_json::from_slice(&std::fs::read(
            writer.path().join("validation/report.json"),
        )?)?;
        require(
            report.observations.len() == 4,
            "failed status prevented later role observations",
        )?;
        require(
            report
                .findings
                .iter()
                .filter(|finding| !finding.passed)
                .count()
                >= 8,
            "gate lost independent findings",
        )?;
        Ok(())
    }

    #[test]
    fn selected_cases_apply_profile_rules_and_reject_unknown_or_duplicate_ids()
    -> validation_support::TestResult {
        use crate::*;
        let mut fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let mut excluded = fixture.suite.cases.first().ok_or("case")?.clone();
        excluded.id = "excluded".into();
        excluded.expected_status = 7;
        fixture.suite.cases.push(excluded);
        let selected = [CaseId::new("last-line")?];
        let request = ExperimentPreparation {
            run: &fixture.request,
            suite: &fixture.suite,
            profile: MeasurementProfile::Full,
            selected_cases: &selected,
            expected_datasets: None,
        };
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            prepare_experiment(
                &fixture.measurement_lock,
                &request,
                &fixture.store,
                &fixture.runner,
            )?,
            &mut writer,
            &fixture.runner,
        )?;
        require(
            validated.report().selected_cases == ["last-line"],
            "selection was not retained",
        )?;
        for invalid in [
            vec![CaseId::new("missing")?],
            vec![CaseId::new("last-line")?, CaseId::new("last-line")?],
        ] {
            require(
                prepare_experiment(
                    &fixture.measurement_lock,
                    &ExperimentPreparation {
                        selected_cases: &invalid,
                        ..request
                    },
                    &fixture.store,
                    &fixture.runner,
                )
                .is_err(),
                "invalid selection accepted",
            )?;
        }
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.profiles.insert(
            MeasurementProfile::Smoke,
            CaseOverride {
                correctness: Some(vec![CorrectnessRule::Literal {
                    stream: Stream::Stdout,
                    text: "different".into(),
                }]),
                ..CaseOverride::default()
            },
        );
        let request = ExperimentPreparation {
            run: &fixture.request,
            suite: &fixture.suite,
            profile: MeasurementProfile::Smoke,
            selected_cases: &selected,
            expected_datasets: None,
        };
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        require(
            validate_experiment(
                prepare_experiment(
                    &fixture.measurement_lock,
                    &request,
                    &fixture.store,
                    &fixture.runner,
                )?,
                &mut writer,
                &fixture.runner,
            )
            .is_err(),
            "profile-specific oracle ignored",
        )?;
        Ok(())
    }

    #[test]
    fn declared_sink_changes_and_directory_mode_mismatches_fail_the_gate()
    -> validation_support::TestResult {
        use crate::*;
        let mut fixture = crate::test_support::validation_fixture(
            "if [ -p /dev/stdout ]; then printf 'EFGH\\n'; else printf different; fi",
            "printf 'EFGH\\n'",
        )?;
        fixture.suite.cases.first_mut().ok_or("case")?.io.stdout = StdoutPolicy::ScratchFile {
            path: "output".into(),
        };
        failed_gate_never_times(&fixture)?;
        drop(fixture);
        let mut fixture = crate::test_support::validation_fixture(
            "/bin/mkdir -m 700 \"$1\"",
            "/bin/mkdir -m 755 \"$1\"",
        )?;
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.argv = vec!["@scratch:dir".into()];
        case.correctness = vec![CorrectnessRule::DirectoryTree {
            paths: vec!["dir".into()],
            compare_mode_to: Some(ComparisonTarget::SelectedBaselines),
        }];
        failed_gate_never_times(&fixture)
    }

    #[test]
    fn directory_paths_only_rule_does_not_add_an_undeclared_mode_comparison()
    -> validation_support::TestResult {
        use crate::*;
        let body = "if [ -p /dev/stdout ]; then mode=700; else mode=755; fi\n/bin/mkdir -m \"$mode\" \"$1\"";
        let mut fixture = crate::test_support::validation_fixture(body, body)?;
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.argv = vec!["@scratch:dir".into()];
        case.correctness = vec![CorrectnessRule::DirectoryTree {
            paths: vec!["dir".into()],
            compare_mode_to: None,
        }];
        case.io.stdout = StdoutPolicy::Discard {};
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        validate_experiment(
            fixture.prepare(crate::MeasurementProfile::Full)?,
            &mut writer,
            &fixture.runner,
        )?;
        Ok(())
    }

    #[test]
    fn every_selected_case_and_both_comparators_are_checked_after_failure()
    -> validation_support::TestResult {
        use crate::*;
        let mut fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        fixture.request.reference = Some(validation_support::script(
            &fixture.request.repository,
            "reference",
            "printf wrong",
        )?);
        let mut second = fixture.suite.cases.first().ok_or("case")?.clone();
        second.id = "second".into();
        fixture.suite.cases.push(second);
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        require(
            validate_experiment(
                fixture.prepare(crate::MeasurementProfile::Full)?,
                &mut writer,
                &fixture.runner,
            )
            .is_err(),
            "wrong reference skipped",
        )?;
        let report: ValidationReport = serde_json::from_slice(&std::fs::read(
            writer.path().join("validation/report.json"),
        )?)?;
        require(
            report.selected_cases == ["last-line", "second"],
            "empty selection omitted cases",
        )?;
        require(
            report.observations.len() == 12,
            "selected role/case observations omitted",
        )?;
        for case in ["last-line", "second"] {
            require(
                report.findings.iter().any(|finding| {
                    finding.case.as_deref() == Some(case)
                        && finding.role == Some(Role::Candidate)
                        && finding.check == "rule-0:Reference"
                        && !finding.passed
                }),
                "candidate/reference comparison omitted",
            )?;
            require(
                report.findings.iter().any(|finding| {
                    finding.case.as_deref() == Some(case)
                        && finding.role == Some(Role::Candidate)
                        && finding.check == "rule-0:Previous"
                        && finding.passed
                }),
                "candidate/previous comparison omitted",
            )?;
        }
        Ok(())
    }

    #[test]
    fn failed_selected_comparator_stays_in_every_comparison_finding()
    -> validation_support::TestResult {
        use crate::*;
        let mut fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let reference = validation_support::script(&fixture.request.repository, "reference", "")?;
        std::fs::write(&reference, b"#!/nonexistent-cli-bench-interpreter\n")?;
        fixture.request.reference = Some(reference);
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        require(
            validate_experiment(
                fixture.prepare(crate::MeasurementProfile::Full)?,
                &mut writer,
                &fixture.runner,
            )
            .is_err(),
            "failed comparator accepted",
        )?;
        let report: ValidationReport = serde_json::from_slice(&std::fs::read(
            writer.path().join("validation/report.json"),
        )?)?;
        require(
            report.findings.iter().any(|finding| {
                finding.role == Some(Role::Candidate)
                    && finding.check == "rule-0:Reference"
                    && !finding.passed
            }),
            "failed selected comparator silently dropped from byte findings",
        )?;
        Ok(())
    }
}
