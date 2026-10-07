//! Compact append-only local history; never compares observations across runs.
use crate::{BenchError, FileIdentity, PublicationRecord, RunBundle};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::Read,
    path::Path,
};
/// Bounded diagnostic prefix plus full byte identity; truncation remains explicit.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryDiagnostic {
    pub file: FileIdentity,
    pub prefix: Vec<u8>,
    pub truncated: bool,
}
/// Saved compact observations and replay provenance, without large resource bytes.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryRecord {
    pub schema_version: u32,
    pub publication: PublicationRecord,
    pub submitted_suite: String,
    pub resolved_suite: String,
    pub checksums: BTreeMap<String, FileIdentity>,
    pub generations: BTreeMap<String, crate::GenerationRecord>,
    pub tools: Option<crate::ReplayTools>,
    pub pipeline: Option<crate::PipelineTools>,
    pub experiment: Option<crate::ExperimentRecord>,
    pub attempt: Option<crate::AttemptRecord>,
    pub diagnostics: BTreeMap<String, HistoryDiagnostic>,
    pub omissions: Vec<String>,
}
/// Select a suite ID; no filter lists all records in stable run-ID order.
#[derive(Clone, Debug, Default)]
pub struct HistoryFilter {
    pub suite: Option<String>,
}
/// Project a run without executing tools. Rendering the publication remains possible after artifact expiry.
/// # Errors
/// Rejects invalid saved evidence or compact metadata exceeding 16 MiB.
pub fn history_record(bundle: &RunBundle) -> Result<HistoryRecord, BenchError> {
    crate::bundle::verify_evidence(bundle)?;
    let mut publication = crate::publication_record(bundle)?;
    publication.replay = "cli-bench replay -i BUNDLE".into();
    let resources = bundle
        .files
        .contains_key("replay-resources.json")
        .then(|| crate::bundle::saved_resources(bundle))
        .transpose()?;
    let mut diagnostics = BTreeMap::new();
    for (name, file) in &bundle.files {
        if name.ends_with(".stderr") || name == "events.jsonl" {
            let mut prefix = Vec::new();
            File::open(crate::bundle::member(&bundle.path, name)?)?
                .take(4096)
                .read_to_end(&mut prefix)?;
            diagnostics.insert(
                name.clone(),
                HistoryDiagnostic {
                    file: file.clone(),
                    truncated: file.bytes > 4096,
                    prefix,
                },
            );
        }
    }
    let (experiment, attempt) = experiment_records(bundle)?;
    let mut omissions = vec!["Executable bytes, dataset bytes and full captures are not retained in compact history; expired or unavailable full artifacts cannot be replayed without exact original resources.".into()];
    if resources.is_none() {
        omissions.push(
            "Original execution resource provenance is unavailable; strict replay is unavailable."
                .into(),
        );
    }
    let record = HistoryRecord {
        schema_version: 1,
        publication,
        submitted_suite: fs::read_to_string(crate::bundle::member(&bundle.path, "suite.toml")?)?,
        resolved_suite: fs::read_to_string(crate::bundle::member(
            &bundle.path,
            "resolved-suite.toml",
        )?)?,
        checksums: bundle.files.clone(),
        generations: crate::bundle::generation_records(bundle)?,
        tools: resources.as_ref().map(|r| r.tools.clone()),
        pipeline: resources.and_then(|r| r.pipeline),
        experiment,
        attempt,
        diagnostics,
        omissions,
    };
    validate(&record)?;
    Ok(record)
}
pub fn experiment_records(
    bundle: &RunBundle,
) -> Result<
    (
        Option<crate::ExperimentRecord>,
        Option<crate::AttemptRecord>,
    ),
    BenchError,
> {
    let Some(request) = &bundle.manifest.experiment else {
        return Ok((None, None));
    };
    crate::suite::validate_identifier(&request.id)?;
    crate::suite::validate_identifier(&bundle.manifest.run_id)?;
    let (root, attempt) = if let Some(root) = crate::bundle::portable_root(bundle) {
        (root.join("experiment"), "attempt.json".into())
    } else {
        (
            bundle
                .path
                .parent()
                .and_then(Path::parent)
                .ok_or_else(|| error("missing store root"))?
                .join("experiments")
                .join(&request.id),
            format!("attempts/{}.json", bundle.manifest.run_id),
        )
    };
    let experiment = root
        .join("experiment.json")
        .symlink_metadata()
        .is_ok()
        .then(|| crate::bundle::read_json(&crate::bundle::member(&root, "experiment.json")?))
        .transpose()?;
    let attempt = root
        .join(&attempt)
        .symlink_metadata()
        .is_ok()
        .then(|| crate::bundle::read_json(&crate::bundle::member(&root, &attempt)?))
        .transpose()?;
    Ok((experiment, attempt))
}
fn validate(record: &HistoryRecord) -> Result<(), BenchError> {
    if record.schema_version != 1 || serde_json::to_vec(record)?.len() > 16_777_216 {
        return Err(error("unsupported or oversized compact history"));
    }
    crate::render_record(
        &record.publication,
        crate::ReportFormat::Json,
        &mut std::io::sink(),
    )?;
    crate::parse_suite(&record.submitted_suite)?;
    let resolved = crate::parse_suite(&record.resolved_suite)?;
    if record
        .publication
        .manifest
        .contract
        .as_ref()
        .is_some_and(|c| c.suite != resolved)
    {
        return Err(error("history suite differs from contract"));
    }
    for identity in record.checksums.values() {
        crate::artifact::validate_identity(identity)?;
    }
    validate_metadata(record)
}
fn validate_metadata(record: &HistoryRecord) -> Result<(), BenchError> {
    let manifest = &record.publication.manifest;
    for (id, generation) in &record.generations {
        crate::dataset::validate_record(generation)?;
        if generation.identity.dataset != *id
            || !manifest
                .inputs
                .iter()
                .any(|i| i.dataset == *id && i.file == generation.file)
            || manifest
                .contract
                .as_ref()
                .is_some_and(|c| c.generator.file != generation.identity.generator.file)
        {
            return Err(error("history generation differs from saved run"));
        }
    }
    if let Some(tools) = &record.tools {
        let contract = manifest
            .contract
            .as_ref()
            .ok_or_else(|| error("tools without resolved contract"))?;
        if tools.harness.identity != contract.harness
            || tools.engine.as_ref().map(|t| &t.identity) != contract.engine.as_ref()
        {
            return Err(error("history tools differ from contract"));
        }
        for tool in [
            Some(&tools.harness),
            tools.engine.as_ref(),
            tools.time.as_ref(),
        ]
        .into_iter()
        .flatten()
        {
            crate::artifact::validate_identity(&tool.identity.file)?;
        }
    }
    if let Some(attempt) = &record.attempt
        && (attempt.schema_version != 1
            || attempt.run_id != manifest.run_id
            || manifest.experiment.as_ref() != Some(&attempt.request))
    {
        return Err(error("history attempt differs from run"));
    }
    if let Some(experiment) = &record.experiment
        && (experiment.schema_version != 1
            || experiment.contract.identity()? != experiment.contract_id
            || (record.publication.result.outcome == crate::RunOutcome::Complete
                && (manifest.contract.as_ref() != Some(&experiment.contract)
                    || manifest
                        .roles
                        .get(&crate::Role::Previous)
                        .and_then(|r| r.build.as_ref())
                        .map(|b| &b.source_sha)
                        != Some(&experiment.starting_sha))))
    {
        return Err(error("history experiment differs from contract"));
    }
    for (name, diagnostic) in &record.diagnostics {
        if record.checksums.get(name) != Some(&diagnostic.file)
            || u64::try_from(diagnostic.prefix.len())
                .map_err(|_| error("diagnostic size overflow"))?
                != diagnostic.file.bytes.min(4096)
            || diagnostic.truncated != (diagnostic.file.bytes > 4096)
        {
            return Err(error("history diagnostic differs from evidence"));
        }
    }
    Ok(())
}
#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct HistoryIndex {
    schema_version: u32,
    runs: BTreeMap<String, FileIdentity>,
}
/// Append immutable compact evidence, with identical repeats as no-ops.
/// A separate local transaction lock serializes index updates, never measurement sessions.
/// # Errors
/// Rejects run-ID content collisions, unsafe members and storage failures.
pub fn append_history(root: &Path, record: &HistoryRecord) -> Result<(), BenchError> {
    validate(record)?;
    fs::create_dir_all(root)?;
    crate::bundle::real_directory(root)?;
    let lock_path = root.join(".history-lock");
    if lock_path.symlink_metadata().is_ok() {
        crate::bundle::member(root, ".history-lock")?;
    }
    let lock = File::options()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(lock_path)?;
    lock.lock()?;
    let runs = root.join("runs");
    fs::create_dir_all(&runs)?;
    crate::bundle::real_directory(&runs)?;
    let mut index = read_index(root)?;
    let id = &record.publication.manifest.run_id;
    crate::suite::validate_identifier(id)?;
    let path = runs.join(format!("{id}.json"));
    let bytes = serde_json::to_vec_pretty(record)?;
    if path.symlink_metadata().is_ok() {
        crate::bundle::member(root, &format!("runs/{id}.json"))?;
        if fs::read(&path)? != bytes {
            return Err(error("history run ID content collision"));
        }
    } else {
        if index.runs.contains_key(id) {
            return Err(error("indexed history record is missing"));
        }
        crate::store::atomic_json(&path, record)?;
    }
    let identity = crate::fingerprint(&path)?;
    if let Some(expected) = index.runs.get(id) {
        if expected != &identity {
            return Err(error("history record differs from index"));
        }
        return Ok(());
    }
    index.runs.insert(id.clone(), identity);
    crate::store::atomic_json(&root.join("history-index.json"), &index)
}
fn read_index(root: &Path) -> Result<HistoryIndex, BenchError> {
    match root.join("history-index.json").symlink_metadata() {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(HistoryIndex {
                schema_version: 1,
                runs: BTreeMap::new(),
            });
        }
        Err(error) => return Err(error.into()),
    }
    let index: HistoryIndex =
        crate::bundle::read_json(&crate::bundle::member(root, "history-index.json")?)?;
    if index.schema_version != 1 {
        return Err(error("unsupported history index"));
    }
    Ok(index)
}
/// List validated compact records or sealed local runs without measurement locks.
/// No ratios are calculated between records, regardless of host identity.
/// # Errors
/// Rejects invalid records, unsafe directories and index mismatches.
pub fn list_history(root: &Path, filter: &HistoryFilter) -> Result<Vec<HistoryRecord>, BenchError> {
    crate::bundle::real_directory(root)?;
    let mut records = Vec::new();
    if root.join("history-index.json").symlink_metadata().is_ok()
        || root.join(".history-lock").symlink_metadata().is_ok()
    {
        let index = read_index(root)?;
        for (id, expected) in index.runs {
            crate::suite::validate_identifier(&id)?;
            let path = crate::bundle::member(root, &format!("runs/{id}.json"))?;
            crate::verify_file(&path, &expected)?;
            let record: HistoryRecord = crate::bundle::read_json(&path)?;
            validate(&record)?;
            if record.publication.manifest.run_id != id {
                return Err(error("history record ID mismatch"));
            }
            records.push(record);
        }
    } else {
        for entry in fs::read_dir(root)? {
            let entry = entry?;
            if entry.file_type()?.is_symlink() {
                return Err(error("symlink history member"));
            }
            if entry.file_type()?.is_dir() {
                records.push(history_record(&crate::load_bundle(&entry.path())?)?);
            }
        }
    }
    records.retain(|record| {
        filter.suite.as_ref().is_none_or(|id| {
            crate::parse_suite(&record.resolved_suite).is_ok_and(|suite| &suite.id == id)
        })
    });
    records.sort_by(|a, b| {
        a.publication
            .manifest
            .run_id
            .cmp(&b.publication.manifest.run_id)
    });
    Ok(records)
}
fn error(message: &str) -> BenchError {
    BenchError::Evidence(message.into())
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{equal, require};
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    #[test]
    fn append_is_idempotent_collisions_fail_and_compact_reports_survive_expiry() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let store = crate::Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let bundle = store
            .begin_run(&suite)?
            .record_failure(crate::RunOutcome::Failed, "retained failure")?;
        let mut record = history_record(&bundle)?;
        record.publication.expires_at_unix_seconds = Some(1);
        let history = root.join("history");
        append_history(&history, &record)?;
        append_history(&history, &record)?;
        let mut before = Vec::new();
        crate::render_record(&record.publication, crate::ReportFormat::Json, &mut before)?;
        std::fs::remove_dir_all(store.root())?;
        let loaded = list_history(&history, &HistoryFilter::default())?;
        equal(&loaded.len(), &1)?;
        let saved = loaded.first().ok_or("missing saved record")?;
        let mut after = Vec::new();
        crate::render_record(&saved.publication, crate::ReportFormat::Json, &mut after)?;
        equal(&before, &after)?;
        record.publication.result.message = Some("conflicting content".into());
        require(
            append_history(&history, &record).is_err(),
            "accepted collision",
        )?;
        fs::remove_file(history.join("history-index.json"))?;
        std::os::unix::fs::symlink(
            history.join("missing-index"),
            history.join("history-index.json"),
        )?;
        require(
            list_history(&history, &HistoryFilter::default()).is_err(),
            "dangling history index became empty success",
        )?;
        Ok(())
    }
    #[test]
    fn early_failed_attempt_keeps_source_and_filters_without_resolved_contract() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let store = crate::Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let request = crate::ExperimentRequest {
            id: "experiment".into(),
            hypothesis: "test hypothesis".into(),
            change_summary: "no improvement".into(),
            requested_previous: "before".into(),
            requested_candidate: "after".into(),
        };
        let bundle = store
            .begin_tagged_run(&suite, &request)?
            .record_failure(crate::RunOutcome::Failed, "unresolved tools")?;
        let record = history_record(&bundle)?;
        require(
            record.publication.manifest.contract.is_none(),
            "invented contract",
        )?;
        equal(
            &record.attempt.as_ref().ok_or("missing attempt")?.request,
            &request,
        )?;
        let portable_path = root.join("portable");
        crate::export_bundle(
            &bundle,
            &crate::ExportRequest {
                destination: portable_path.clone(),
                with_inputs: false,
                with_binaries: false,
            },
        )?;
        let portable = crate::load_bundle(&portable_path)?;
        equal(
            &serde_json::to_vec(&history_record(&portable)?)?,
            &serde_json::to_vec(&record)?,
        )?;
        let history = root.join("history");
        append_history(&history, &record)?;
        equal(
            &list_history(
                &history,
                &HistoryFilter {
                    suite: Some(suite.id),
                },
            )?
            .len(),
            &1,
        )?;
        equal(
            &list_history(
                &history,
                &HistoryFilter {
                    suite: Some("different".into()),
                },
            )?
            .len(),
            &0,
        )?;
        std::fs::write(
            history
                .join("runs")
                .join(format!("{}.json", bundle.manifest.run_id)),
            "changed",
        )?;
        require(
            list_history(&history, &HistoryFilter::default()).is_err(),
            "accepted changed history",
        )?;
        Ok(())
    }
    #[test]
    fn legacy_run_retains_available_cached_generation_recipe() -> TestResult {
        use crate::*;
        let fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let prepared = fixture.prepare(MeasurementProfile::Smoke)?;
        let generator = prepared.roles().generator.as_ref().ok_or("generator")?;
        let harness = BoundTool {
            path: generator.path.clone(),
            identity: ToolIdentity {
                file: generator.artifact.file.clone(),
                version: "fixture harness".into(),
            },
        };
        let host = collect_host(&fixture.suite.environment);
        let mut bundle = run(
            &RunRequest {
                preparation: ExperimentPreparation {
                    run: &fixture.request,
                    suite: &fixture.suite,
                    profile: MeasurementProfile::Smoke,
                    selected_cases: &[],
                    expected_datasets: None,
                },
                submitted_toml: None,
                measurement_lock: &fixture.measurement_lock,
                harness: &harness,
                host: &host,
                mode: RunMode::CheckOnly { engine: None },
                experiment: None,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        // Represent the earlier sealed format, which kept generation provenance only in the cache.
        std::fs::remove_file(bundle.path.join("replay-resources.json"))?;
        bundle.files.remove("replay-resources.json");
        crate::store::atomic_json(
            &bundle.path.join("checksums.json"),
            &serde_json::json!({"schema_version": 1, "files": bundle.files}),
        )?;
        let record = history_record(&bundle)?;
        require(
            record.generations.contains_key("tiny"),
            "lost available generation recipe",
        )?;
        let mut changed = record.clone();
        changed
            .generations
            .get_mut("tiny")
            .ok_or("generation")?
            .file
            .sha256 = "0".repeat(64);
        require(
            append_history(&fixture.root.join("bad-history"), &changed).is_err(),
            "accepted inconsistent generation evidence",
        )?;
        let path = fixture.root.join("portable");
        export_bundle(
            &bundle,
            &ExportRequest {
                destination: path.clone(),
                with_inputs: false,
                with_binaries: false,
            },
        )?;
        let portable = load_bundle(&path)?;
        std::fs::remove_dir_all(fixture.store.root())?;
        equal(&record.generations, &history_record(&portable)?.generations)?;
        Ok(())
    }
    #[test]
    fn persisted_unequal_binary_sizes_keep_exact_float_analysis() -> TestResult {
        use crate::*;
        let fixture =
            crate::test_support::validation_fixture("printf 'wrong'", "printf 'EFGH\\n'")?;
        let harness = crate::test_support::timing_support::engine(&fixture, "")?;
        let host = collect_host(&fixture.suite.environment);
        let bundle = run(
            &RunRequest {
                preparation: ExperimentPreparation {
                    run: &fixture.request,
                    suite: &fixture.suite,
                    profile: MeasurementProfile::Full,
                    selected_cases: &[],
                    expected_datasets: None,
                },
                submitted_toml: None,
                measurement_lock: &fixture.measurement_lock,
                harness: &harness,
                host: &host,
                mode: RunMode::CheckOnly { engine: None },
                experiment: None,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        equal(&bundle.result.outcome, &RunOutcome::Failed)?;
        let record = history_record(&bundle)?;
        let comparison = record
            .publication
            .analysis
            .cases
            .first()
            .and_then(|c| c.comparisons.first())
            .ok_or("comparison")?;
        equal(
            &comparison.size_change_percent,
            &Some(-3.846_153_846_153_843_6),
        )?;
        let mut bytes = Vec::new();
        render_record(&record.publication, ReportFormat::Json, &mut bytes)?;
        let reloaded: PublicationRecord = serde_json::from_slice(&bytes)?;
        let mut after = Vec::new();
        render_record(&reloaded, ReportFormat::Json, &mut after)?;
        equal(&bytes, &after)?;
        let directory = fixture.root.join("history");
        append_history(&directory, &record)?;
        let saved = list_history(&directory, &HistoryFilter::default())?;
        equal(
            &saved.first().ok_or("history")?.publication.analysis,
            &record.publication.analysis,
        )?;
        Ok(())
    }
}
