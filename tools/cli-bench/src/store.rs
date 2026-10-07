use crate::{
    ArtifactRecord, AttemptRecord, BenchError, ExperimentRecord, ExperimentRequest, FileIdentity,
    Role, RunEvent, RunManifest, RunOutcome, RunResult, Suite, fingerprint, verify_file,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const MARKER: &[u8] = b"cli-bench evidence store v1\n";

/// Marked evidence root, independent of Cargo build caches. Clones share filesystem locks.
#[derive(Clone, Debug)]
pub struct Store {
    root: PathBuf,
}
/// Exclusive owner of a newly allocated run. Dropping retains an incomplete run.
#[derive(Debug)]
pub struct RunWriter {
    store: Store,
    path: PathBuf,
    id: String,
    suite: Suite,
    experiment: Option<ExperimentRequest>,
    execution_kind: Option<crate::ExecutionKind>,
    checked_experiment_manifest: Option<String>,
    events: File,
    pub(crate) budget: crate::budget::EvidenceBudget,
    _lock: File,
}
/// Verified frozen run and its evidence files; paths remain owned by the caller's store.
#[derive(Debug)]
pub struct RunBundle {
    pub path: PathBuf,
    pub manifest: RunManifest,
    pub result: RunResult,
    pub files: BTreeMap<String, FileIdentity>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Seal {
    schema_version: u32,
    files: BTreeMap<String, FileIdentity>,
}

impl Store {
    /// Open/create a marked evidence root; reject unmarked nonempty roots and symlinks.
    /// Relative paths are resolved against the caller's current directory without changing it.
    ///
    /// # Errors
    /// Returns errors for unsafe/conflicting roots or failed filesystem operations.
    pub fn open(root: &Path) -> Result<Self, BenchError> {
        fs::create_dir_all(root)?;
        reject_symlink(root)?;
        let root = fs::canonicalize(root)?;
        let marker = root.join(".cli-bench-store");
        if !marker.exists() {
            if fs::read_dir(&root)?.next().is_some() {
                return Err(evidence("refusing an unmarked nonempty evidence root"));
            }
            match File::options().write(true).create_new(true).open(&marker) {
                Ok(mut file) => {
                    file.write_all(MARKER)?;
                    file.sync_all()?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error.into()),
            }
        }
        reject_symlink(&marker)?;
        if fs::read(&marker)? != MARKER {
            return Err(evidence("unrecognized evidence root marker"));
        }
        let store = Self { root };
        let _lock = store.transaction_lock()?;
        for directory in ["artifacts", "datasets", "runs", "experiments", "history"] {
            let path = store.root.join(directory);
            fs::create_dir_all(&path)?;
            reject_symlink(&path)?;
        }
        Ok(store)
    }
    /// Open an existing marked store without creating directories, locks or files.
    /// # Errors
    /// Rejects missing/unmarked roots, symlinks and unreadable metadata.
    pub fn open_existing(root: &Path) -> Result<Self, BenchError> {
        reject_symlink(root)?;
        let root = fs::canonicalize(root)?;
        let marker = root.join(".cli-bench-store");
        reject_symlink(&marker)?;
        if fs::read(marker)? != MARKER {
            return Err(evidence("unrecognized evidence root marker"));
        }
        Ok(Self { root })
    }
    /// The canonical evidence root.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }
    pub(crate) fn transaction_lock(&self) -> Result<File, BenchError> {
        let path = self.root.join(".lock");
        if path.symlink_metadata().is_ok() {
            reject_symlink(&path)?;
        }
        let lock = File::options()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        lock.lock()?;
        Ok(lock)
    }
    /// Begin an independent run using a serialized equivalent of the supplied suite.
    ///
    /// # Errors
    /// Fails for invalid suites, allocation failures or failed durable initial writes.
    pub fn begin_run(&self, suite: &Suite) -> Result<RunWriter, BenchError> {
        crate::validate_suite(suite)?;
        let source = toml::to_string(suite).map_err(|error| evidence(error.to_string()))?;
        self.begin_source(suite, &source, None)
    }
    /// Preserve exact submitted TOML while beginning a validated run.
    ///
    /// # Errors
    /// Fails for invalid suite text or filesystem failures.
    pub fn begin_run_source(&self, source: &str) -> Result<RunWriter, BenchError> {
        let suite = crate::parse_suite(source)?;
        self.begin_source(&suite, source, None)
    }
    /// Preserve submitted TOML separately from resolved build overrides and optional attempt metadata.
    /// # Errors
    /// Rejects unrelated suite changes, invalid source/settings, or evidence writes.
    pub fn begin_resolved_run(
        &self,
        source: &str,
        resolved: &Suite,
        experiment: Option<&ExperimentRequest>,
    ) -> Result<RunWriter, BenchError> {
        let mut submitted = crate::parse_suite(source)?;
        crate::validate_suite(resolved)?;
        submitted.build.clone_from(&resolved.build);
        if submitted != *resolved {
            return Err(evidence(
                "resolved suite differs outside declared build overrides",
            ));
        }
        if let Some(experiment) = experiment {
            validate_experiment_request(experiment)?;
        }
        self.begin_source(resolved, source, experiment)
    }
    fn begin_source(
        &self,
        suite: &Suite,
        source: &str,
        experiment: Option<&ExperimentRequest>,
    ) -> Result<RunWriter, BenchError> {
        let path = unique_directory(&self.root.join("runs"), "run")?;
        let id = path
            .file_name()
            .and_then(|part| part.to_str())
            .ok_or_else(|| evidence("non-UTF-8 run identifier"))?
            .to_owned();
        let lock = File::options()
            .read(true)
            .write(true)
            .create_new(true)
            .open(path.join(".lock"))?;
        lock.lock()?;
        let budget =
            crate::budget::EvidenceBudget::new(path.clone(), suite.limits.max_evidence_bytes);
        let write = |name: &str, bytes: &[u8]| -> Result<(), BenchError> {
            budget.reserve_write(bytes.len())?;
            atomic_write(&path.join(name), bytes)
        };
        write(
            "status.json",
            &serde_json::to_vec_pretty(&RunResult {
                schema_version: 1,
                outcome: RunOutcome::Incomplete,
                message: Some("run has not finalized".into()),
            })?,
        )?;
        for directory in ["correctness", "raw", "raw/timing", "raw/rss"] {
            fs::create_dir(path.join(directory))?;
        }
        let events = File::options()
            .append(true)
            .create_new(true)
            .open(path.join("events.jsonl"))?;
        let writer = RunWriter {
            store: self.clone(),
            path,
            id,
            suite: suite.clone(),
            experiment: None,
            execution_kind: None,
            checked_experiment_manifest: None,
            events,
            budget,
            _lock: lock,
        };
        let writer = if let Some(request) = experiment {
            self.tag_writer(writer, request)?
        } else {
            writer
        };
        writer.write_bytes(&writer.path.join("suite.toml"), source.as_bytes())?;
        let effective = toml::to_string(suite).map_err(|error| evidence(error.to_string()))?;
        writer.write_bytes(
            &writer.path.join("resolved-suite.toml"),
            effective.as_bytes(),
        )?;
        Ok(writer)
    }
    /// Start and immediately retain a tagged invocation, including unresolved/failed attempts.
    ///
    /// # Errors
    /// Rejects unsafe IDs, missing descriptions/bindings, or failed evidence writes.
    pub fn begin_tagged_run(
        &self,
        suite: &Suite,
        request: &ExperimentRequest,
    ) -> Result<RunWriter, BenchError> {
        validate_experiment_request(request)?;
        crate::validate_suite(suite)?;
        let source = toml::to_string(suite).map_err(|error| evidence(error.to_string()))?;
        self.begin_source(suite, &source, Some(request))
    }
    fn tag_writer(
        &self,
        mut writer: RunWriter,
        request: &ExperimentRequest,
    ) -> Result<RunWriter, BenchError> {
        writer.experiment = Some(request.clone());
        writer.write_json(&writer.path.join("request.json"), request)?;
        let _lock = self.transaction_lock()?;
        let path = self.root.join("experiments").join(&request.id);
        fs::create_dir_all(path.join("attempts"))?;
        reject_symlink(&path)?;
        reject_symlink(&path.join("attempts"))?;
        atomic_json(
            &path.join("attempts").join(format!("{}.json", writer.id)),
            &AttemptRecord {
                schema_version: 1,
                run_id: writer.id.clone(),
                request: request.clone(),
            },
        )?;
        Ok(writer)
    }
    /// Path to the retained executable; no arbitrary source path is stored in its identity.
    #[must_use]
    pub fn artifact_path(&self, record: &ArtifactRecord) -> PathBuf {
        self.root
            .join("artifacts")
            .join(&record.id)
            .join("executable")
    }
    /// Verify both the executable and its original immutable provenance record.
    ///
    /// # Errors
    /// Rejects invalid identifiers, tampering, missing evidence or changed metadata.
    pub fn verify_artifact(&self, record: &ArtifactRecord) -> Result<(), BenchError> {
        if record.schema_version != 1
            || record.id != crate::artifact::json_identity(&(1_u32, &record.file, &record.build))?
        {
            return Err(evidence("invalid artifact identity"));
        }
        if let Some(build) = &record.build {
            build.validate()?;
        }
        let path = self.artifact_path(record);
        reject_symlink(path.parent().ok_or_else(|| evidence("missing parent"))?)?;
        verify_file(&path, &record.file)?;
        let stored: ArtifactRecord = read_json(&path.with_file_name("build.json"))?;
        if stored != *record {
            return Err(evidence("artifact provenance mismatch"));
        }
        Ok(())
    }
    /// Reload a finalized bundle and verify every retained evidence hash.
    ///
    /// # Errors
    /// Rejects unsealed, altered or malformed bundles; sealed incomplete outcomes remain readable.
    pub fn load_run(&self, id: &str) -> Result<RunBundle, BenchError> {
        identifier(id)?;
        let path = self.root.join("runs").join(id);
        reject_symlink(&path)?;
        let status: RunResult = read_json(&path.join("status.json"))?;
        let result: RunResult = read_json(&path.join("result.json"))?;
        if status != result {
            return Err(evidence("run is not finalized"));
        }
        let seal: Seal = read_json(&path.join("checksums.json"))?;
        if seal.schema_version != 1 {
            return Err(evidence("unsupported evidence schema"));
        }
        for (relative, expected) in &seal.files {
            relative_path(relative)?;
            verify_file(&path.join(relative), expected)?;
        }
        let actual = inventory(&path)?;
        if actual != seal.files {
            return Err(evidence("evidence inventory changed"));
        }
        let manifest: RunManifest = read_json(&path.join("manifest.json"))?;
        if manifest.run_id != id {
            return Err(evidence("run identity mismatch"));
        }
        Ok(RunBundle {
            path,
            manifest,
            result,
            files: seal.files,
        })
    }
}
fn validate_experiment_request(request: &ExperimentRequest) -> Result<(), BenchError> {
    identifier(&request.id)?;
    for value in [
        &request.hypothesis,
        &request.change_summary,
        &request.requested_previous,
        &request.requested_candidate,
    ] {
        if value.trim().is_empty() || value.contains('\0') {
            return Err(evidence(
                "experiment requires descriptions and revision bindings",
            ));
        }
    }
    Ok(())
}

impl RunWriter {
    /// Record known requested stages for early failure evidence; source-only callers may leave them unresolved.
    pub const fn set_execution_kind(&mut self, kind: crate::ExecutionKind) {
        self.execution_kind = Some(kind);
    }
    pub(crate) fn matches_suite(&self, suite: &Suite) -> bool {
        &self.suite == suite
    }
    /// Caller-owned evidence directory for raw streams and stage records.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }
    /// Unique identifier that is never reused, including after failed finalization.
    #[must_use]
    pub fn id(&self) -> &str {
        &self.id
    }
    /// Append and flush one typed JSON event; propagation of I/O failure is mandatory.
    ///
    /// # Errors
    /// Fails when serialization, append or durable flush fails.
    pub fn append_event(&mut self, event: &RunEvent) -> Result<(), BenchError> {
        let mut bytes = serde_json::to_vec(event)?;
        bytes.push(b'\n');
        self.budget.reserve_write(bytes.len())?;
        self.events.write_all(&bytes)?;
        self.events.flush()?;
        self.events.sync_all()?;
        Ok(())
    }
    pub(crate) fn write_json<T: Serialize>(
        &self,
        path: &Path,
        value: &T,
    ) -> Result<(), BenchError> {
        self.write_bytes(path, &serde_json::to_vec_pretty(value)?)
    }
    pub(crate) fn write_bytes(&self, path: &Path, bytes: &[u8]) -> Result<(), BenchError> {
        self.budget.reserve_write(bytes.len())?;
        atomic_write(path, bytes)
    }
    pub(crate) fn finalization_bytes(
        manifest: &RunManifest,
        result: &RunResult,
    ) -> Result<usize, BenchError> {
        let result_bytes = serde_json::to_vec_pretty(result)?.len();
        crate::budget::sum_sizes(&[
            serde_json::to_vec_pretty(manifest)?.len(),
            result_bytes,
            result_bytes,
            serde_json::to_vec(&RunEvent::Stage(format!("finalizing {:?}", result.outcome)))?.len(),
            1,
        ])
    }
    /// Finalize once, checking resolved identities before publishing terminal status.
    /// Any error retains the initial incomplete status and any available evidence.
    ///
    /// # Errors
    /// Fails on invalid bindings, experiment conflicts, tampering or filesystem failures.
    pub fn finish(
        mut self,
        manifest: &RunManifest,
        result: &RunResult,
    ) -> Result<RunBundle, BenchError> {
        if result.outcome != RunOutcome::Complete {
            self.budget.fail();
        }
        let outcome = self.finish_inner(manifest, result);
        if let Err(error) = &outcome {
            self.budget.fail();
            let _ = self.append_event(&RunEvent::Failure(error.to_string()));
            let _ = self.write_json(
                &self.path.join("status.json"),
                &RunResult {
                    schema_version: 1,
                    outcome: RunOutcome::Incomplete,
                    message: Some(error.to_string()),
                },
            );
        }
        outcome
    }
    fn finish_inner(
        &mut self,
        manifest: &RunManifest,
        result: &RunResult,
    ) -> Result<RunBundle, BenchError> {
        self.validate_manifest(manifest, result)?;
        self.budget
            .ensure_capacity(Self::finalization_bytes(manifest, result)?)?;
        self.write_json(&self.path.join("manifest.json"), manifest)?;
        self.write_json(&self.path.join("result.json"), result)?;
        self.append_event(&RunEvent::Stage(format!("finalizing {:?}", result.outcome)))?;
        if self.checked_experiment_manifest.as_ref()
            != Some(&crate::artifact::json_identity(manifest)?)
        {
            self.bind_experiment(manifest)?;
        }
        let files = inventory(&self.path)?;
        atomic_json(
            &self.path.join("checksums.json"),
            &Seal {
                schema_version: 1,
                files: files.clone(),
            },
        )?;
        File::open(&self.path)?.sync_all()?;
        // All evidence and its checksum index are durable before terminal status is visible.
        self.write_json(&self.path.join("status.json"), result)?;
        Ok(RunBundle {
            path: self.path.clone(),
            manifest: manifest.clone(),
            result: result.clone(),
            files,
        })
    }
    /// Retain an incomplete/failed outcome with genuinely unresolved identities absent.
    ///
    /// # Errors
    /// Rejects success outcomes; propagates any storage failure.
    pub fn record_failure(
        mut self,
        outcome: RunOutcome,
        message: &str,
    ) -> Result<RunBundle, BenchError> {
        if outcome == RunOutcome::Complete {
            return Err(evidence("failure recording cannot complete a run"));
        }
        self.budget.fail();
        self.append_event(&RunEvent::Failure(message.into()))?;
        let manifest = RunManifest {
            schema_version: 1,
            execution_kind: self.execution_kind,
            run_id: self.id.clone(),
            contract: None,
            roles: BTreeMap::new(),
            inputs: Vec::new(),
            selected_cases: Vec::new(),
            host: None,
            tool_paths: None,
            experiment: self.experiment.clone(),
        };
        self.finish(
            &manifest,
            &RunResult {
                schema_version: 1,
                outcome,
                message: Some(message.into()),
            },
        )
    }
    fn validate_manifest(
        &self,
        manifest: &RunManifest,
        result: &RunResult,
    ) -> Result<(), BenchError> {
        if manifest.schema_version != 1
            || result.schema_version != 1
            || manifest.run_id != self.id
            || manifest.experiment != self.experiment
        {
            return Err(evidence("run manifest binding mismatch"));
        }
        if result.outcome == RunOutcome::Complete
            && (manifest.contract.is_none()
                || !manifest.roles.contains_key(&Role::Candidate)
                || manifest.selected_cases.is_empty()
                || manifest.host.is_none()
                || manifest.tool_paths.is_none())
        {
            return Err(evidence("successful run has unresolved identities"));
        }
        if let Some(contract) = &manifest.contract {
            contract.identity()?;
            if let Some(paths) = &manifest.tool_paths {
                for (path, identity) in [
                    (&paths.harness, &contract.harness),
                    (&paths.generator, &contract.generator),
                ] {
                    if result.outcome == RunOutcome::Complete {
                        verify_file(path, &identity.file)?;
                    }
                }
                match (&paths.engine, &contract.engine) {
                    (Some(path), Some(identity)) => {
                        if result.outcome == RunOutcome::Complete {
                            verify_file(path, &identity.file)?;
                        }
                    }
                    (None, None) => {}
                    _ => return Err(evidence("engine binding mismatch")),
                }
            } else if result.outcome == RunOutcome::Complete {
                return Err(evidence("missing tool paths"));
            }
            if contract.suite != self.suite {
                return Err(evidence("contract suite differs from submitted suite"));
            }
        }
        let mut cases = std::collections::BTreeSet::new();
        for case in &manifest.selected_cases {
            if !cases.insert(case) || !self.suite.cases.iter().any(|declared| &declared.id == case)
            {
                return Err(evidence("invalid selected case"));
            }
        }
        for record in manifest.roles.values() {
            crate::artifact::validate_identity(&record.file)?;
            if let Some(build) = &record.build {
                build.validate()?;
            }
            if result.outcome == RunOutcome::Complete {
                self.store.verify_artifact(record)?;
            }
        }
        let mut inputs = std::collections::BTreeSet::new();
        for input in &manifest.inputs {
            crate::artifact::validate_identity(&input.file)?;
            if !inputs.insert(&input.dataset)
                || !self
                    .suite
                    .datasets
                    .iter()
                    .any(|dataset| dataset.id == input.dataset)
            {
                return Err(evidence("invalid input dataset"));
            }
            if result.outcome == RunOutcome::Complete {
                verify_file(&input.path, &input.file)?;
            }
        }
        if result.outcome == RunOutcome::Complete {
            for dataset in required_inputs(manifest, &self.suite) {
                if !inputs.iter().any(|id| id.as_str() == dataset) {
                    return Err(evidence(format!("missing required input {dataset}")));
                }
            }
        }
        Ok(())
    }
    pub(crate) fn freeze_experiment(&mut self, manifest: &RunManifest) -> Result<(), BenchError> {
        if self.experiment.is_some() {
            // The composed runner records rejected preflight attempts as Failed;
            // direct finish callers still undergo the existing contract checks.
            self.checked_experiment_manifest = Some(crate::artifact::json_identity(manifest)?);
            self.validate_manifest(
                manifest,
                &RunResult {
                    schema_version: 1,
                    outcome: RunOutcome::Complete,
                    message: None,
                },
            )?;
            self.bind_experiment(manifest)?;
        }
        Ok(())
    }
    pub(crate) fn check_evidence_limit(&self) -> Result<(), BenchError> {
        self.budget.remaining().map(|_| ())
    }
    fn bind_experiment(&self, manifest: &RunManifest) -> Result<(), BenchError> {
        let (Some(request), Some(contract)) = (&self.experiment, &manifest.contract) else {
            return Ok(());
        };
        if contract.build.is_none() || contract.engine.is_none() {
            return Err(evidence(
                "tagged experiment requires resolved build and engine",
            ));
        }
        for (role, artifact) in &manifest.roles {
            if let Some(build) = &artifact.build
                && Some(&build.policy) != contract.build.as_ref()
            {
                return Err(evidence(format!(
                    "{role:?} build policy differs from experiment contract"
                )));
            }
        }
        // validate_manifest verifies tool bytes only when their bindings are present.
        // Retain partial failed manifests without treating their claimed identities as verified.
        if manifest.tool_paths.is_none() {
            return Ok(());
        }
        let Some(previous) = manifest
            .roles
            .get(&Role::Previous)
            .and_then(|record| record.build.as_ref())
        else {
            return Err(evidence(
                "resolved experiment requires a Git-built previous role",
            ));
        };
        let record = ExperimentRecord {
            schema_version: 1,
            starting_sha: previous.source_sha.clone(),
            contract: contract.clone(),
            contract_id: contract.identity()?,
        };
        let _lock = self.store.transaction_lock()?;
        let path = self
            .store
            .root
            .join("experiments")
            .join(&request.id)
            .join("experiment.json");
        if path.exists() {
            let existing: ExperimentRecord = read_json(&path)?;
            if existing != record {
                return Err(evidence(
                    "experiment starting SHA or measurement contract changed",
                ));
            }
        }
        let input_root = path.with_file_name("inputs");
        fs::create_dir_all(&input_root)?;
        reject_symlink(&input_root)?;
        for input in &manifest.inputs {
            let path = input_root.join(format!("{}.json", input.dataset));
            if path.exists() {
                let known: FileIdentity = read_json(&path)?;
                if known != input.file {
                    return Err(evidence(
                        "experiment input identity changed for an unchanged recipe",
                    ));
                }
            }
        }
        if !path.exists() {
            atomic_json(&path, &record)?;
        }
        for input in &manifest.inputs {
            let path = input_root.join(format!("{}.json", input.dataset));
            if !path.exists() {
                atomic_json(&path, &input.file)?;
            }
        }
        File::open(input_root)?.sync_all()?;
        File::open(
            path.parent()
                .ok_or_else(|| evidence("missing experiment parent"))?,
        )?
        .sync_all()?;
        Ok(())
    }
}

fn required_inputs(manifest: &RunManifest, suite: &Suite) -> std::collections::BTreeSet<String> {
    let mut found = std::collections::BTreeSet::new();
    for case in suite
        .cases
        .iter()
        .filter(|case| manifest.selected_cases.contains(&case.id))
    {
        let mut resolved = case.clone();
        if let Some(overrides) = manifest
            .contract
            .as_ref()
            .and_then(|contract| case.profiles.get(&contract.profile))
        {
            if let Some(argv) = &overrides.argv {
                resolved.argv.clone_from(argv);
            }
            if let Some(argv) = &overrides.role_argv {
                resolved.role_argv.clone_from(argv);
            }
            if let Some(rules) = &overrides.correctness {
                resolved.correctness.clone_from(rules);
            }
        }
        resolved.profiles.clear();
        for arg in resolved
            .argv
            .iter()
            .chain(resolved.role_argv.values().flatten())
        {
            if let Some(id) = arg.strip_prefix("@input:") {
                found.insert(id.into());
            }
        }
        match &resolved.io.stdin {
            crate::StdinPolicy::RegularFile { dataset } | crate::StdinPolicy::Pipe { dataset } => {
                found.insert(dataset.clone());
            }
            crate::StdinPolicy::Null {} => {}
        }
        for rule in &resolved.correctness {
            if let crate::CorrectnessRule::TailSlice { dataset, .. } = rule {
                found.insert(dataset.clone());
            }
        }
    }
    found
}

pub fn atomic_json<T: Serialize>(path: &Path, value: &T) -> Result<(), BenchError> {
    atomic_write(path, &serde_json::to_vec_pretty(value)?)
}
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), BenchError> {
    atomic_write_with(path, bytes, File::sync_all, |a, b| fs::rename(a, b))
}
fn atomic_write_with<F, R>(path: &Path, bytes: &[u8], flush: F, rename: R) -> Result<(), BenchError>
where
    F: FnOnce(&File) -> std::io::Result<()>,
    R: FnOnce(&Path, &Path) -> std::io::Result<()>,
{
    let temporary = path.with_extension("pending");
    let mut file = File::options()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    let result = (|| {
        file.write_all(bytes)?;
        file.flush()?;
        flush(&file)?;
        rename(&temporary, path)?;
        Ok::<(), BenchError>(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}
pub fn unique_directory(parent: &Path, prefix: &str) -> Result<PathBuf, BenchError> {
    let time = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| evidence(error.to_string()))?
        .as_nanos();
    for attempt in 0..1024_u32 {
        let path = parent.join(format!("{prefix}-{time}-{}-{attempt}", std::process::id()));
        match fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error.into()),
        }
    }
    Err(evidence("unable to allocate unique evidence directory"))
}
fn identifier(value: &str) -> Result<(), BenchError> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(evidence("invalid evidence identifier"));
    }
    Ok(())
}
fn relative_path(value: &str) -> Result<(), BenchError> {
    if value.is_empty()
        || value
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == ".." || part.contains('\\'))
    {
        return Err(evidence("invalid evidence path"));
    }
    Ok(())
}
fn reject_symlink(path: &Path) -> Result<(), BenchError> {
    if path.symlink_metadata()?.file_type().is_symlink() {
        return Err(evidence("symlinks are not allowed in evidence storage"));
    }
    Ok(())
}
fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, BenchError> {
    reject_symlink(path)?;
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
fn evidence(message: impl Into<String>) -> BenchError {
    BenchError::Evidence(message.into())
}
pub fn inventory(root: &Path) -> Result<BTreeMap<String, FileIdentity>, BenchError> {
    fn walk(
        root: &Path,
        directory: &Path,
        files: &mut BTreeMap<String, FileIdentity>,
    ) -> Result<(), BenchError> {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let path = entry.path();
            reject_symlink(&path)?;
            if entry.file_type()?.is_dir() {
                walk(root, &path, files)?;
                continue;
            }
            let relative = path
                .strip_prefix(root)
                .map_err(|error| evidence(error.to_string()))?
                .to_str()
                .ok_or_else(|| evidence("non-UTF-8 evidence path"))?
                .to_owned();
            if matches!(
                relative.as_str(),
                ".lock" | "status.json" | "checksums.json"
            ) {
                continue;
            }
            let file = File::open(&path)?;
            file.sync_all()?;
            files.insert(relative, fingerprint(&path)?);
        }
        File::open(directory)?.sync_all()?;
        Ok(())
    }
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    Ok(files)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
        if condition {
            Ok(())
        } else {
            Err(message.into())
        }
    }
    fn suite() -> Result<Suite, BenchError> {
        crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))
    }
    #[test]
    fn failed_flush_never_marks_complete() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let path = root.path().join("status.json");
        let result = atomic_write_with(
            &path,
            b"complete",
            |_| Err(std::io::Error::other("injected flush")),
            |a, b| std::fs::rename(a, b),
        );
        require(result.is_err(), "injected I/O failure was hidden")?;
        require(!path.exists(), "failed write published terminal status")?;
        Ok(())
    }
    #[test]
    fn failed_rename_never_publishes() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let path = root.path().join("status.json");
        let result = atomic_write_with(&path, b"complete", std::fs::File::sync_all, |_, _| {
            Err(std::io::Error::other("injected rename"))
        });
        require(result.is_err(), "injected I/O failure was hidden")?;
        require(!path.exists(), "failed write published terminal status")?;
        Ok(())
    }
    #[test]
    fn run_directory_is_never_reused() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(root.path())?;
        let a = store.begin_run(&suite()?)?;
        let path = a.path().to_path_buf();
        drop(a);
        let b = store.begin_run(&suite()?)?;
        require(path != b.path(), "run path was reused")?;
        require(
            path.join("status.json").exists(),
            "incomplete status missing",
        )?;
        Ok(())
    }
    #[test]
    fn concurrent_runs_do_not_share_output() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(root.path())?;
        let suite = suite()?;
        let paths = std::thread::scope(|scope| {
            let a = scope.spawn(|| store.begin_run(&suite));
            let b = scope.spawn(|| store.begin_run(&suite));
            (a.join(), b.join())
        });
        let (Ok(a), Ok(b)) = paths else {
            return Err("worker failed".into());
        };
        require(a?.path() != b?.path(), "concurrent runs share output")?;
        Ok(())
    }
    #[test]
    fn rejects_unmarked_roots_and_owns_run_lock() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        fs::write(root.path().join("unrelated"), b"keep")?;
        require(
            Store::open(root.path()).is_err(),
            "unmarked user directory was accepted",
        )?;
        let store = Store::open(&root.path().join("evidence"))?;
        let run = store.begin_run(&suite()?)?;
        let second = File::options()
            .read(true)
            .write(true)
            .open(run.path().join(".lock"))?;
        require(second.try_lock().is_err(), "run lock was not held")?;
        drop(run);
        second.try_lock()?;
        Ok(())
    }

    #[test]
    fn failed_finalization_retains_error_and_incomplete_status()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(root.path())?;
        let run = store.begin_run(&suite()?)?;
        let path = run.path().to_path_buf();
        // A pre-existing temporary sibling deterministically injects a create-new write failure.
        fs::write(path.join("result.pending"), b"conflict")?;
        require(
            run.record_failure(RunOutcome::Failed, "earlier execution failed")
                .is_err(),
            "write failure hidden",
        )?;
        let status: RunResult = read_json(&path.join("status.json"))?;
        require(
            status.outcome == RunOutcome::Incomplete,
            "failed finalization published terminal status",
        )?;
        require(status.message.is_some(), "storage failure was not retained")?;
        require(
            !path.join("checksums.json").exists(),
            "failed run was sealed",
        )?;
        Ok(())
    }
    #[test]
    fn literal_expectations_are_not_input_bindings() -> Result<(), Box<dyn std::error::Error>> {
        let mut suite = suite()?;
        let case = suite.cases.first_mut().ok_or("missing case")?;
        case.purpose = "@input:not-a-binding".into();
        case.correctness.push(crate::CorrectnessRule::Literal {
            stream: crate::Stream::Stdout,
            text: "@input:literal-content".into(),
        });
        let manifest: RunManifest =
            serde_json::from_str(include_str!("../tests/inputs/failed-manifest.json"))?;
        let manifest = RunManifest {
            selected_cases: vec!["last-line".into()],
            ..manifest
        };
        let required = required_inputs(&manifest, &suite);
        require(
            required == ["tiny".to_owned()].into(),
            "literal prose/expectations treated as an input binding",
        )?;
        Ok(())
    }
    #[test]
    fn initial_exact_source_cannot_bypass_normal_evidence_cap()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("store"))?;
        let mut suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        suite.limits.max_evidence_bytes = 256;
        let source = toml::to_string(&suite)?;
        crate::test_support::require(
            store.begin_run_source(&source).is_err(),
            "oversized source escaped initial cap",
        )?;
        let paths: Vec<_> = fs::read_dir(store.root().join("runs"))?.collect::<Result<_, _>>()?;
        let path = paths
            .first()
            .ok_or("missing retained run directory")?
            .path();
        crate::test_support::require(
            path.join("status.json").is_file() && !path.join("suite.toml").exists(),
            "initial failure evidence not honest",
        )?;
        crate::test_support::require(
            crate::budget::size(&path)? <= 256,
            "initial writes exceeded cap",
        )?;
        Ok(())
    }
    #[test]
    fn oversized_tagged_source_retains_discoverable_initial_attempt()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("store"))?;
        let mut suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        suite.limits.max_evidence_bytes = 1_024;
        let source = format!("# {}\n{}", "x".repeat(2_048), toml::to_string(&suite)?);
        let request = ExperimentRequest {
            id: "oversized".into(),
            hypothesis: "hypothesis".into(),
            change_summary: "change".into(),
            requested_previous: "HEAD".into(),
            requested_candidate: "candidate".into(),
        };
        crate::test_support::require(
            store
                .begin_resolved_run(&source, &suite, Some(&request))
                .is_err(),
            "oversized tagged source accepted",
        )?;
        crate::test_support::equal(
            &fs::read_dir(store.root().join("experiments/oversized/attempts"))?.count(),
            &1,
        )?;
        Ok(())
    }
    #[test]
    fn early_failure_retains_explicit_execution_kind() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        for kind in [
            crate::ExecutionKind::CheckOnly,
            crate::ExecutionKind::Measure,
        ] {
            let mut writer = store.begin_run(&suite)?;
            writer.set_execution_kind(kind);
            let bundle = writer.record_failure(RunOutcome::Failed, "tool discovery failed")?;
            crate::test_support::equal(
                &store
                    .load_run(&bundle.manifest.run_id)?
                    .manifest
                    .execution_kind,
                &Some(kind),
            )?;
        }
        Ok(())
    }
    #[test]
    fn resolved_run_retains_exact_submitted_text_and_separate_effective_build()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("evidence"))?;
        let source = format!(
            "# exact submitted comment\n{}\n",
            include_str!("../tests/inputs/minimal-suite.toml")
        );
        let mut resolved = crate::parse_suite(&source)?;
        resolved.build.features = vec!["selected-feature".into()];
        let experiment = ExperimentRequest {
            id: "source-attempt".into(),
            hypothesis: "hypothesis".into(),
            change_summary: "change".into(),
            requested_previous: "HEAD".into(),
            requested_candidate: "candidate".into(),
        };
        for tag in [None, Some(&experiment)] {
            let writer = store.begin_resolved_run(&source, &resolved, tag)?;
            crate::test_support::equal(
                &fs::read_to_string(writer.path().join("suite.toml"))?,
                &source,
            )?;
            crate::test_support::equal(
                &crate::parse_suite(&fs::read_to_string(
                    writer.path().join("resolved-suite.toml"),
                )?)?,
                &resolved,
            )?;
            let failed = writer.record_failure(RunOutcome::Failed, "before tool discovery")?;
            crate::test_support::require(
                store
                    .load_run(&failed.manifest.run_id)?
                    .files
                    .contains_key("resolved-suite.toml"),
                "resolved source unsealed",
            )?;
        }
        Ok(())
    }
    #[test]
    fn resolved_source_rejects_changes_outside_build_overrides()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("evidence"))?;
        let source = include_str!("../tests/inputs/minimal-suite.toml");
        let mut resolved = crate::parse_suite(source)?;
        resolved.cases.first_mut().ok_or("case")?.purpose = "changed scenario".into();
        crate::test_support::require(
            store.begin_resolved_run(source, &resolved, None).is_err(),
            "unrelated resolved changes accepted",
        )?;
        Ok(())
    }
    #[test]
    fn sealed_incomplete_runs_load_but_unfinished_runs_do_not()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("evidence"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let writer = store.begin_run(&suite)?;
        let id = writer.id().to_owned();
        drop(writer);
        crate::test_support::require(store.load_run(&id).is_err(), "unfinished run accepted")?;
        let bundle = store
            .begin_run(&suite)?
            .record_failure(RunOutcome::Incomplete, "RSS unavailable")?;
        crate::test_support::equal(
            &store.load_run(&bundle.manifest.run_id)?.result.outcome,
            &RunOutcome::Incomplete,
        )?;
        Ok(())
    }
}
