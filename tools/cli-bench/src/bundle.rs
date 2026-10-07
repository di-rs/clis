//! Portable directory evidence and explicit strict replay.
use crate::{BenchError, FileIdentity, RunBundle};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    path::{Component, Path, PathBuf},
};

/// Resource copies are opt-in; an existing destination is never reused.
#[derive(Debug)]
pub struct ExportRequest {
    pub destination: PathBuf,
    pub with_inputs: bool,
    pub with_binaries: bool,
}
/// A resource's expected bytes and optional contained portable binding.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BundleResource {
    pub file: FileIdentity,
    pub path: Option<String>,
}
/// Hash inventory of every exported member, excluding this index itself.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BundleIndex {
    pub schema_version: u32,
    pub files: BTreeMap<String, FileIdentity>,
    pub resources: BTreeMap<String, BundleResource>,
    pub omissions: Vec<String>,
}
/// Copy frozen evidence into a new directory without locks, child processes or archives.
/// # Errors
/// Rejects existing destinations, changed evidence, unsafe members and unavailable requested bytes.
pub fn export_bundle(
    bundle: &RunBundle,
    request: &ExportRequest,
) -> Result<BundleIndex, BenchError> {
    verify_evidence(bundle)?;
    if request.destination.symlink_metadata().is_ok() {
        return Err(error("export destination already exists"));
    }
    let parent = request
        .destination
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    real_directory(parent)?;
    let staging = crate::store::unique_directory(parent, "export-pending")?;
    let result = export_to(bundle, request, &staging);
    match result {
        Ok(index) => {
            // create_dir is the no-clobber reservation; rename into it only after checking it is empty.
            fs::create_dir(&request.destination)?;
            fs::rename(&staging, &request.destination)?;
            File::open(parent)?.sync_all()?;
            Ok(index)
        }
        Err(err) => {
            let _ = fs::remove_dir_all(&staging);
            Err(err)
        }
    }
}
fn export_to(
    bundle: &RunBundle,
    request: &ExportRequest,
    root: &Path,
) -> Result<BundleIndex, BenchError> {
    fs::create_dir(root.join("evidence"))?;
    for (name, identity) in &bundle.files {
        copy_verified(
            &member(&bundle.path, name)?,
            &root.join("evidence").join(name),
            identity,
        )?;
    }
    let mut index = BundleIndex {
        schema_version: 1,
        files: BTreeMap::new(),
        resources: BTreeMap::new(),
        omissions: vec![],
    };
    let resources = resource_sources(bundle)?;
    for (name, (path, identity)) in resources {
        crate::suite::validate_identifier(&name)?;
        let include = if name.starts_with("input-") {
            request.with_inputs
        } else {
            request.with_binaries
        };
        let relative = if include {
            let name = format!("resources/{name}");
            copy_verified(&path, &root.join(&name), &identity)?;
            Some(name)
        } else {
            index
                .omissions
                .push(format!("{name}: bytes omitted; exact identity retained"));
            None
        };
        index.resources.insert(
            name,
            BundleResource {
                file: identity,
                path: relative,
            },
        );
    }
    if !bundle.files.contains_key("replay-resources.json") {
        index
            .omissions
            .push("strict replay execution resources unavailable in original evidence".into());
    }
    let generations = generation_records(bundle)?;
    if !generations.is_empty() {
        fs::create_dir(root.join("metadata"))?;
        crate::store::atomic_json(&root.join("metadata/generations.json"), &generations)?;
    }
    for input in &bundle.manifest.inputs {
        if !generations.contains_key(&input.dataset) {
            index
                .omissions
                .push(format!("{}: generation recipe unavailable", input.dataset));
        }
    }
    copy_experiment(bundle, root)?;
    index.files = inventory(root)?;
    crate::store::atomic_json(&root.join("bundle-index.json"), &index)?;
    Ok(index)
}
fn copy_verified(
    source: &Path,
    destination: &Path,
    identity: &FileIdentity,
) -> Result<(), BenchError> {
    crate::verify_file(source, identity)?;
    fs::create_dir_all(
        destination
            .parent()
            .ok_or_else(|| error("missing copy parent"))?,
    )?;
    let mut input = File::open(source)?;
    let mut output = File::options()
        .write(true)
        .create_new(true)
        .open(destination)?;
    std::io::copy(&mut input, &mut output)?;
    output.set_permissions(input.metadata()?.permissions())?;
    output.sync_all()?;
    crate::verify_file(source, identity)?;
    crate::verify_file(destination, identity)
}
/// Load a sealed store run or portable directory, verifying all retained member hashes.
/// Original absolute paths in the manifest are evidence, never portable resource bindings.
/// # Errors
/// Rejects altered, escaping, symlinked, missing, extra or unsealed evidence.
pub fn load_bundle(path: &Path) -> Result<RunBundle, BenchError> {
    real_directory(path)?;
    let path = fs::canonicalize(path)?;
    if path.join("bundle-index.json").symlink_metadata().is_ok() {
        let index = load_index(&path)?;
        let evidence = member(&path, "evidence")?;
        let manifest = read_json(&member(&evidence, "manifest.json")?)?;
        let result = read_json(&member(&evidence, "result.json")?)?;
        let files = index
            .files
            .into_iter()
            .filter_map(|(name, identity)| {
                name.strip_prefix("evidence/")
                    .map(|name| (name.to_owned(), identity))
            })
            .collect();
        let bundle = RunBundle {
            path: evidence,
            manifest,
            result,
            files,
        };
        crate::render_record(
            &crate::publication_record(&bundle)?,
            crate::ReportFormat::Json,
            &mut std::io::sink(),
        )?;
        return Ok(bundle);
    }
    let runs = path
        .parent()
        .ok_or_else(|| error("missing runs directory"))?;
    if runs.file_name().is_none_or(|name| name != "runs") {
        return Err(error(
            "expected sealed local run or portable bundle directory",
        ));
    }
    inventory(&path)?;
    crate::Store::open_existing(runs.parent().ok_or_else(|| error("missing store"))?)?.load_run(
        path.file_name()
            .and_then(|s| s.to_str())
            .ok_or_else(|| error("invalid run ID"))?,
    )
}
fn load_index(root: &Path) -> Result<BundleIndex, BenchError> {
    let index: BundleIndex = read_json(&member(root, "bundle-index.json")?)?;
    if index.schema_version != 1 {
        return Err(error("unsupported bundle schema"));
    }
    for (name, identity) in &index.files {
        crate::verify_file(&member(root, name)?, identity)?;
    }
    if inventory(root)? != index.files {
        return Err(error("bundle inventory changed"));
    }
    for resource in index.resources.values() {
        crate::artifact::validate_identity(&resource.file)?;
        if let Some(name) = &resource.path {
            if !name.starts_with("resources/") || index.files.get(name) != Some(&resource.file) {
                return Err(error("unindexed resource binding"));
            }
            member(root, name)?;
        }
    }
    Ok(index)
}
pub fn portable_root(bundle: &RunBundle) -> Option<&Path> {
    let parent = bundle.path.parent()?;
    (bundle.path.file_name().is_some_and(|n| n == "evidence")
        && parent.join("bundle-index.json").symlink_metadata().is_ok())
    .then_some(parent)
}
pub fn verify_evidence(bundle: &RunBundle) -> Result<(), BenchError> {
    let stored = load_bundle(portable_root(bundle).unwrap_or(&bundle.path))?;
    if stored.manifest != bundle.manifest
        || stored.result != bundle.result
        || stored.files != bundle.files
    {
        return Err(error("bundle differs from sealed evidence"));
    }
    Ok(())
}

pub fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, BenchError> {
    crate::fingerprint(path)?;
    Ok(serde_json::from_reader(File::open(path)?)?)
}
pub fn real_directory(path: &Path) -> Result<(), BenchError> {
    if !path.symlink_metadata()?.is_dir() {
        return Err(error("expected real directory, not symlink"));
    }
    Ok(())
}
pub fn member(root: &Path, name: &str) -> Result<PathBuf, BenchError> {
    if name.is_empty()
        || name.contains('\\')
        || Path::new(name)
            .components()
            .any(|p| !matches!(p, Component::Normal(_)))
        || name
            .split('/')
            .any(|s| s.is_empty() || s == "." || s == "..")
    {
        return Err(error("unsafe bundle member"));
    }
    let path = root.join(name);
    let mut current = root.to_path_buf();
    real_directory(root)?;
    for component in Path::new(name).components() {
        current.push(component);
        if current.symlink_metadata()?.file_type().is_symlink() {
            return Err(error("symlink bundle member"));
        }
    }
    Ok(path)
}
fn inventory(root: &Path) -> Result<BTreeMap<String, FileIdentity>, BenchError> {
    fn walk(
        root: &Path,
        directory: &Path,
        files: &mut BTreeMap<String, FileIdentity>,
    ) -> Result<(), BenchError> {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let path = entry.path();
            let name = path
                .strip_prefix(root)
                .map_err(|_| error("invalid inventory root"))?
                .to_str()
                .ok_or_else(|| error("non-UTF-8 bundle member"))?
                .to_owned();
            member(root, &name)?;
            if entry.file_type()?.is_dir() {
                walk(root, &path, files)?;
            } else if name != "bundle-index.json" {
                files.insert(name, crate::fingerprint(&path)?);
            }
        }
        Ok(())
    }
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    Ok(files)
}
fn error(message: impl Into<String>) -> BenchError {
    BenchError::Evidence(message.into())
}
fn resource_sources(
    bundle: &RunBundle,
) -> Result<BTreeMap<String, (PathBuf, FileIdentity)>, BenchError> {
    if let Some(root) = portable_root(bundle) {
        return Ok(load_index(root)?
            .resources
            .into_iter()
            .map(|(name, r)| {
                (
                    name,
                    (r.path.map_or_else(PathBuf::new, |p| root.join(p)), r.file),
                )
            })
            .collect());
    }
    let mut sources = BTreeMap::new();
    for input in &bundle.manifest.inputs {
        crate::suite::validate_identifier(&input.dataset)?;
        sources.insert(
            format!("input-{}", input.dataset),
            (input.path.clone(), input.file.clone()),
        );
    }
    let store = bundle
        .path
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| error("missing store root"))?;
    for (role, artifact) in &bundle.manifest.roles {
        sources.insert(
            format!("role-{}", role_name(*role)),
            (
                store
                    .join("artifacts")
                    .join(&artifact.id)
                    .join("executable"),
                artifact.file.clone(),
            ),
        );
    }
    if let (Some(contract), Some(paths)) = (&bundle.manifest.contract, &bundle.manifest.tool_paths)
    {
        sources.insert(
            "generator".into(),
            (paths.generator.clone(), contract.generator.file.clone()),
        );
        sources.insert(
            "harness".into(),
            (paths.harness.clone(), contract.harness.file.clone()),
        );
        if let (Some(engine), Some(path)) = (&contract.engine, &paths.engine) {
            sources.insert("engine".into(), (path.clone(), engine.file.clone()));
        }
    }
    if bundle.files.contains_key("replay-resources.json") {
        let resources = saved_resources(bundle)?;
        if let Some(time) = resources.tools.time {
            sources.insert("time".into(), (time.path, time.identity.file));
        }
        if let Some(pipeline) = resources.pipeline {
            sources.insert(
                "bash".into(),
                (pipeline.bash.path, pipeline.bash.identity.file),
            );
            sources.insert(
                "cat".into(),
                (pipeline.cat.path, pipeline.cat.identity.file),
            );
        }
    }
    Ok(sources)
}
pub fn generation_records(
    bundle: &RunBundle,
) -> Result<BTreeMap<String, crate::GenerationRecord>, BenchError> {
    let records = if bundle.files.contains_key("replay-resources.json") {
        saved_resources(bundle)?.datasets.generations
    } else if let Some(root) = portable_root(bundle) {
        let index = load_index(root)?;
        if index.files.contains_key("metadata/generations.json") {
            read_json(&member(root, "metadata/generations.json")?)?
        } else {
            BTreeMap::new()
        }
    } else {
        let datasets = bundle
            .path
            .parent()
            .and_then(Path::parent)
            .ok_or_else(|| error("missing store root"))?
            .join("datasets");
        let mut records = BTreeMap::new();
        for input in &bundle.manifest.inputs {
            let Some(directory) = input
                .path
                .ancestors()
                .take_while(|p| p.starts_with(&datasets))
                .find(|p| p.join("generation.json").symlink_metadata().is_ok())
            else {
                continue;
            };
            let relative = directory
                .join("generation.json")
                .strip_prefix(&datasets)
                .map_err(|_| error("invalid dataset cache binding"))?
                .to_str()
                .ok_or_else(|| error("non-UTF-8 dataset cache binding"))?
                .to_owned();
            records.insert(
                input.dataset.clone(),
                read_json(&member(&datasets, &relative)?)?,
            );
        }
        records
    };
    for (id, record) in &records {
        crate::dataset::validate_record(record)?;
        let input = bundle
            .manifest
            .inputs
            .iter()
            .find(|i| &i.dataset == id)
            .ok_or_else(|| error("unmatched generation record"))?;
        if record.identity.dataset != *id
            || record.file != input.file
            || bundle
                .manifest
                .contract
                .as_ref()
                .is_some_and(|c| c.generator.file != record.identity.generator.file)
        {
            return Err(error("generation record differs from saved run"));
        }
    }
    Ok(records)
}
fn copy_experiment(bundle: &RunBundle, root: &Path) -> Result<(), BenchError> {
    let Some(experiment) = &bundle.manifest.experiment else {
        return Ok(());
    };
    crate::suite::validate_identifier(&experiment.id)?;
    crate::suite::validate_identifier(&bundle.manifest.run_id)?;
    let source = if let Some(portable) = portable_root(bundle) {
        portable.join("experiment")
    } else {
        bundle
            .path
            .parent()
            .and_then(Path::parent)
            .ok_or_else(|| error("missing store"))?
            .join("experiments")
            .join(&experiment.id)
    };
    for (name, destination) in [
        ("experiment.json".to_owned(), "experiment.json"),
        (
            format!("attempts/{}.json", bundle.manifest.run_id),
            "attempt.json",
        ),
    ] {
        let source = if portable_root(bundle).is_some() {
            source.join(destination)
        } else {
            source.join(name)
        };
        if source.symlink_metadata().is_ok() {
            real_directory(
                source
                    .parent()
                    .ok_or_else(|| error("missing experiment parent"))?,
            )?;
            copy_verified(
                &source,
                &root.join("experiment").join(destination),
                &crate::fingerprint(&source)?,
            )?;
        }
    }
    Ok(())
}
const fn role_name(role: crate::Role) -> &'static str {
    match role {
        crate::Role::Candidate => "candidate",
        crate::Role::Previous => "previous",
        crate::Role::Reference => "reference",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    #[test]
    fn failed_evidence_exports_offline_and_rejects_tampering_and_existing_destination() -> TestResult
    {
        let root = assert_fs::TempDir::new()?;
        let store = crate::Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let bundle = store
            .begin_run(&suite)?
            .record_failure(crate::RunOutcome::Failed, "retained failure")?;
        let request = ExportRequest {
            destination: root.join("export"),
            with_inputs: false,
            with_binaries: false,
        };
        export_bundle(&bundle, &request)?;
        let portable = load_bundle(&request.destination)?;
        crate::test_support::equal(&portable.result, &bundle.result)?;
        let expected_replay = format!(
            "cli-bench replay -i {}",
            shell_words::quote(crate::process::utf8_path(&fs::canonicalize(
                &request.destination
            )?)?)
        );
        crate::test_support::equal(
            &crate::publication_record(&portable)?.replay,
            &expected_replay,
        )?;
        crate::test_support::require(
            export_bundle(&bundle, &request).is_err(),
            "overwrote destination",
        )?;
        std::fs::write(portable.path.join("suite.toml"), "changed")?;
        crate::test_support::require(
            load_bundle(&request.destination).is_err(),
            "accepted tampering",
        )?;
        Ok(())
    }
    #[test]
    fn export_requires_a_seal_even_for_a_caller_constructed_bundle() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let store = crate::Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let bundle = store
            .begin_run(&suite)?
            .record_failure(crate::RunOutcome::Failed, "failure")?;
        fs::remove_file(bundle.path.join("checksums.json"))?;
        crate::test_support::require(
            export_bundle(
                &bundle,
                &ExportRequest {
                    destination: root.join("portable"),
                    with_inputs: false,
                    with_binaries: false,
                },
            )
            .is_err(),
            "exported unsealed evidence",
        )?;
        Ok(())
    }
    #[test]
    fn portable_reports_and_history_keep_assigned_expiry() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let store = crate::Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let mut bundle = store
            .begin_run(&suite)?
            .record_failure(crate::RunOutcome::Failed, "failure")?;
        let mut publication = crate::publication_record(&bundle)?;
        publication.expires_at_unix_seconds = Some(12345);
        crate::store::atomic_json(&bundle.path.join("publication.json"), &publication)?;
        bundle.files.insert(
            "publication.json".into(),
            crate::fingerprint(&bundle.path.join("publication.json"))?,
        );
        crate::store::atomic_json(
            &bundle.path.join("checksums.json"),
            &serde_json::json!({"schema_version": 1, "files": bundle.files}),
        )?;
        let path = root.join("portable");
        export_bundle(
            &bundle,
            &ExportRequest {
                destination: path.clone(),
                with_inputs: false,
                with_binaries: false,
            },
        )?;
        let portable = load_bundle(&path)?;
        let regenerated = crate::publication_record(&portable)?;
        crate::test_support::equal(&regenerated.expires_at_unix_seconds, &Some(12345))?;
        crate::test_support::equal(
            &crate::history_record(&portable)?
                .publication
                .expires_at_unix_seconds,
            &Some(12345),
        )?;
        Ok(())
    }
    #[test]
    fn rejects_symlink_members_and_escaping_index_paths() -> TestResult {
        for escape in [false, true] {
            let root = assert_fs::TempDir::new()?;
            let store = crate::Store::open(&root.join("store"))?;
            let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
            let bundle = store
                .begin_run(&suite)?
                .record_failure(crate::RunOutcome::Incomplete, "unfinished stage")?;
            let destination = root.join("portable");
            let mut index = export_bundle(
                &bundle,
                &ExportRequest {
                    destination: destination.clone(),
                    with_inputs: false,
                    with_binaries: false,
                },
            )?;
            if escape {
                index.files.insert(
                    "../outside".into(),
                    crate::fingerprint(&bundle.path.join("suite.toml"))?,
                );
                crate::store::atomic_json(&destination.join("bundle-index.json"), &index)?;
            } else {
                let path = destination.join("evidence/suite.toml");
                fs::remove_file(&path)?;
                std::os::unix::fs::symlink(bundle.path.join("suite.toml"), &path)?;
            }
            crate::test_support::require(
                load_bundle(&destination).is_err(),
                "accepted unsafe portable member",
            )?;
        }
        Ok(())
    }
}

/// Explicit caller-owned execution resources for a strict replay.
pub struct ReplayContext<'a> {
    pub measurement_lock: &'a crate::MeasurementLock,
    pub harness: &'a crate::BoundTool,
    pub host: &'a crate::HostMetadata,
    pub mode: crate::RunMode<'a>,
    pub cache_root: &'a Path,
}
/// Identified tools retained for replay; paths are empty when portable bytes were omitted.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayTools {
    pub harness: crate::BoundTool,
    pub engine: Option<crate::BoundTool>,
    pub time: Option<crate::BoundTool>,
    pub platform: Option<crate::Platform>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayResources {
    pub schema_version: u32,
    pub roles: BTreeMap<crate::Role, crate::BoundExecutable>,
    pub generator: crate::BoundExecutable,
    pub datasets: crate::DatasetSet,
    pub pipeline: Option<crate::PipelineTools>,
    pub tools: ReplayTools,
}
pub fn capture_resources(
    prepared: &crate::PreparedExperiment<'_>,
    request: &crate::RunRequest<'_>,
    writer: &crate::RunWriter,
) -> Result<(), BenchError> {
    writer.write_json(
        &writer.path().join("replay-resources.json"),
        &resource_record(prepared, request)?,
    )
}
pub fn resource_record(
    prepared: &crate::PreparedExperiment<'_>,
    request: &crate::RunRequest<'_>,
) -> Result<ReplayResources, BenchError> {
    let (engine, time, platform) = match request.mode {
        crate::RunMode::CheckOnly { engine } => (engine.cloned(), None, None),
        crate::RunMode::Measure {
            engine,
            time,
            platform,
        } => (Some(engine.clone()), time.cloned(), Some(platform)),
    };
    let resources = ReplayResources {
        schema_version: 1,
        roles: prepared.roles().roles.clone(),
        generator: prepared
            .roles()
            .generator
            .clone()
            .ok_or_else(|| error("missing generator"))?,
        datasets: prepared.datasets().clone(),
        pipeline: prepared.roles().pipeline.clone(),
        tools: ReplayTools {
            harness: request.harness.clone(),
            engine,
            time,
            platform,
        },
    };
    Ok(resources)
}
pub fn saved_resources(bundle: &RunBundle) -> Result<ReplayResources, BenchError> {
    if !bundle.files.contains_key("replay-resources.json") {
        return Err(replay_error("saved execution resources unavailable"));
    }
    let resources: ReplayResources = read_json(&member(&bundle.path, "replay-resources.json")?)?;
    if resources.schema_version != 1 {
        return Err(replay_error("unsupported replay resource schema"));
    }
    Ok(resources)
}
fn rebound_path(bundle: &RunBundle, name: &str, original: &Path) -> Result<PathBuf, BenchError> {
    let Some(root) = portable_root(bundle) else {
        return Ok(original.to_path_buf());
    };
    let index = load_index(root)?;
    Ok(index
        .resources
        .get(name)
        .and_then(|r| r.path.as_ref())
        .map_or_else(PathBuf::new, |p| root.join(p)))
}
/// Read saved bindings, rebasing portable copies and leaving omitted paths empty.
/// Caller supplies fresh owned HOME/config directories and can replace omitted paths explicitly.
/// # Errors
/// Rejects invalid evidence or unavailable execution provenance.
pub fn replay_bindings(
    bundle: &RunBundle,
    home: &Path,
    config: &Path,
) -> Result<crate::RoleBindings, BenchError> {
    verify_evidence(bundle)?;
    let mut resources = saved_resources(bundle)?;
    for (role, bound) in &mut resources.roles {
        bound.path = rebound_path(bundle, &format!("role-{}", role_name(*role)), &bound.path)?;
    }
    resources.generator.path = rebound_path(bundle, "generator", &resources.generator.path)?;
    if let Some(pipeline) = &mut resources.pipeline {
        pipeline.bash.path = rebound_path(bundle, "bash", &pipeline.bash.path)?;
        pipeline.cat.path = rebound_path(bundle, "cat", &pipeline.cat.path)?;
    }
    Ok(crate::RoleBindings {
        roles: resources.roles,
        generator: Some(resources.generator),
        environment: bundle
            .manifest
            .contract
            .as_ref()
            .ok_or_else(|| replay_error("unresolved contract"))?
            .suite
            .environment
            .clone(),
        home: home.into(),
        config: config.into(),
        pipeline: resources.pipeline,
    })
}
/// Read saved tool identities and contained portable paths; never discover or execute tools.
/// # Errors
/// Rejects unavailable or altered replay evidence.
pub fn replay_tools(bundle: &RunBundle) -> Result<ReplayTools, BenchError> {
    verify_evidence(bundle)?;
    let mut tools = saved_resources(bundle)?.tools;
    tools.harness.path = rebound_path(bundle, "harness", &tools.harness.path)?;
    for (name, tool) in [("engine", &mut tools.engine), ("time", &mut tools.time)] {
        if let Some(tool) = tool {
            tool.path = rebound_path(bundle, name, &tool.path)?;
        }
    }
    Ok(tools)
}
/// Owned immutable recipe; execution resources are borrowed only when run.
pub struct ReplayRecipe {
    contract: crate::MeasurementContract,
    kind: crate::ExecutionKind,
    source: String,
    cases: Vec<crate::CaseId>,
    datasets: crate::DatasetSet,
    bindings: crate::RoleBindings,
    tools: ReplayTools,
    experiment: Option<crate::ExperimentRequest>,
}
/// Plan replay without discovering tools, collecting a host or acquiring a lock.
/// # Errors
/// Rejects missing or changed resources, settings and unresolved contracts.
pub fn replay_request(
    bundle: &RunBundle,
    bindings: &crate::RoleBindings,
) -> Result<ReplayRecipe, BenchError> {
    verify_evidence(bundle)?;
    let contract = bundle
        .manifest
        .contract
        .clone()
        .ok_or_else(|| replay_error("unresolved measurement contract"))?;
    contract.identity()?;
    verify_experiment_anchor(bundle, &contract)?;
    if contract.validator_policy != "correctness-v1"
        || contract.analysis_policy != "descriptive-v1"
        || contract.generator.version
            != "version unavailable; retained executable identified by SHA-256"
    {
        return Err(replay_error("unsupported measurement contract"));
    }
    let kind = bundle
        .manifest
        .execution_kind
        .ok_or_else(|| replay_error("unresolved requested execution kind"))?;
    if bundle.manifest.selected_cases.is_empty() {
        return Err(replay_error("unresolved case selection"));
    }
    let resources = saved_resources(bundle)?;
    if resources.roles.len() != bundle.manifest.roles.len()
        || bindings.roles.len() != resources.roles.len()
        || bindings.environment != contract.suite.environment
    {
        return Err(replay_error("role selection or environment changed"));
    }
    for (role, expected) in &bundle.manifest.roles {
        let saved = resources
            .roles
            .get(role)
            .ok_or_else(|| replay_error("missing saved role"))?;
        let actual = bindings
            .roles
            .get(role)
            .ok_or_else(|| replay_error("missing bound role"))?;
        if &saved.artifact != expected || &actual.artifact != expected {
            return Err(replay_error("role artifact or build provenance changed"));
        }
        verify_replay_file(&actual.path, &expected.file)?;
    }
    let generator = bindings
        .generator
        .as_ref()
        .ok_or_else(|| replay_error("missing Biggie binding"))?;
    if generator.artifact != resources.generator.artifact
        || generator.artifact.file != contract.generator.file
    {
        return Err(replay_error("Biggie identity or build provenance changed"));
    }
    verify_replay_file(&generator.path, &generator.artifact.file)?;
    if resources.tools.harness.identity != contract.harness
        || resources.tools.engine.as_ref().map(|t| &t.identity) != contract.engine.as_ref()
    {
        return Err(replay_error("saved tool contract mismatch"));
    }
    verify_pipeline(resources.pipeline.as_ref(), bindings.pipeline.as_ref())?;
    if resources.datasets.inputs.len() != bundle.manifest.inputs.len() {
        return Err(replay_error("dataset selection changed"));
    }
    for input in &bundle.manifest.inputs {
        let saved = resources
            .datasets
            .inputs
            .get(&input.dataset)
            .ok_or_else(|| replay_error("missing input recipe"))?;
        if saved.file != input.file {
            return Err(replay_error("input identity changed"));
        }
    }
    let source = fs::read_to_string(member(&bundle.path, "suite.toml")?)?;
    let resolved = crate::parse_suite(&fs::read_to_string(member(
        &bundle.path,
        "resolved-suite.toml",
    )?)?)?;
    if resolved != contract.suite {
        return Err(replay_error("resolved suite differs from frozen contract"));
    }
    Ok(ReplayRecipe {
        contract,
        kind,
        source,
        cases: bundle
            .manifest
            .selected_cases
            .iter()
            .map(|c| crate::CaseId::new(c.clone()))
            .collect::<Result<_, _>>()?,
        datasets: resources.datasets,
        bindings: bindings.clone(),
        tools: resources.tools,
        experiment: bundle.manifest.experiment.clone(),
    })
}
impl ReplayRecipe {
    /// Execute via the shared guarded run pipeline, with fresh paths and a new run ID.
    /// # Errors
    /// Rejects context identity changes before execution and propagates run storage errors.
    pub fn execute(
        &self,
        context: &ReplayContext<'_>,
        store: &crate::Store,
        runner: &crate::ProcessRunner,
    ) -> Result<RunBundle, BenchError> {
        verify_tool(context.harness, &self.contract.harness)?;
        verify_pipeline(
            self.bindings.pipeline.as_ref(),
            self.bindings.pipeline.as_ref(),
        )?;
        let (kind, engine, time, platform) = match context.mode {
            crate::RunMode::CheckOnly { engine } => {
                (crate::ExecutionKind::CheckOnly, engine, None, None)
            }
            crate::RunMode::Measure {
                engine,
                time,
                platform,
            } => (
                crate::ExecutionKind::Measure,
                Some(engine),
                time,
                Some(platform),
            ),
        };
        if kind != self.kind || platform != self.tools.platform {
            return Err(replay_error(
                "requested execution kind or RSS platform changed",
            ));
        }
        for (actual, expected) in [
            (engine, self.tools.engine.as_ref()),
            (time, self.tools.time.as_ref()),
        ] {
            match (actual, expected) {
                (Some(actual), Some(expected)) => verify_tool(actual, &expected.identity)?,
                (None, None) => {}
                _ => return Err(replay_error("execution tool selection changed")),
            }
        }
        let retained = |role| {
            self.bindings
                .roles
                .get(&role)
                .cloned()
                .map(Box::new)
                .map(crate::ExecutableSource::Retained)
        };
        let roles = crate::RoleRequest {
            repository: context.cache_root.into(),
            candidate: retained(crate::Role::Candidate)
                .ok_or_else(|| replay_error("missing candidate"))?,
            previous: retained(crate::Role::Previous),
            reference: self
                .bindings
                .roles
                .get(&crate::Role::Reference)
                .map(|b| b.path.clone()),
            generator: self
                .bindings
                .generator
                .clone()
                .map(Box::new)
                .map(crate::ExecutableSource::Retained),
            git: None,
            tools: None,
            cache_root: context.cache_root.into(),
            home: self.bindings.home.clone(),
            config: self.bindings.config.clone(),
            pipeline: self.bindings.pipeline.clone(),
        };
        crate::run(
            &crate::RunRequest {
                preparation: crate::ExperimentPreparation {
                    run: &roles,
                    suite: &self.contract.suite,
                    profile: self.contract.profile,
                    selected_cases: &self.cases,
                    expected_datasets: Some(&self.datasets),
                },
                submitted_toml: Some(&self.source),
                measurement_lock: context.measurement_lock,
                harness: context.harness,
                host: context.host,
                mode: context.mode,
                experiment: self.experiment.as_ref(),
            },
            store,
            runner,
        )
    }
}
fn verify_experiment_anchor(
    bundle: &RunBundle,
    contract: &crate::MeasurementContract,
) -> Result<(), BenchError> {
    if bundle.manifest.experiment.is_none() {
        return Ok(());
    }
    let (anchor, attempt) = crate::history::experiment_records(bundle)?;
    let anchor = anchor.ok_or_else(|| replay_error("saved experiment anchor unavailable"))?;
    let attempt = attempt.ok_or_else(|| replay_error("saved attempt unavailable"))?;
    let starting_sha = bundle
        .manifest
        .roles
        .get(&crate::Role::Previous)
        .and_then(|r| r.build.as_ref())
        .map(|b| &b.source_sha);
    if anchor.schema_version != 1
        || anchor.contract.identity()? != anchor.contract_id
        || &anchor.contract != contract
        || starting_sha != Some(&anchor.starting_sha)
        || attempt.schema_version != 1
        || attempt.run_id != bundle.manifest.run_id
        || Some(&attempt.request) != bundle.manifest.experiment.as_ref()
    {
        return Err(replay_error(
            "attempt differs from frozen experiment contract or starting SHA",
        ));
    }
    Ok(())
}
fn verify_pipeline(
    expected: Option<&crate::PipelineTools>,
    actual: Option<&crate::PipelineTools>,
) -> Result<(), BenchError> {
    match (expected, actual) {
        (Some(expected), Some(actual)) => {
            for (expected, actual) in [(&expected.bash, &actual.bash), (&expected.cat, &actual.cat)]
            {
                verify_tool(actual, &expected.identity)?;
            }
        }
        (None, None) => {}
        _ => return Err(replay_error("pipeline bindings changed")),
    }
    Ok(())
}
fn verify_tool(
    actual: &crate::BoundTool,
    expected: &crate::ToolIdentity,
) -> Result<(), BenchError> {
    if &actual.identity != expected {
        return Err(replay_error("tool identity changed"));
    }
    verify_replay_file(&actual.path, &expected.file)
}
fn verify_replay_file(path: &Path, identity: &FileIdentity) -> Result<(), BenchError> {
    crate::verify_file(path, identity)
        .map_err(|e| replay_error(format!("missing or changed exact resource: {e}")))
}
fn replay_error(message: impl std::fmt::Display) -> BenchError {
    error(format!(
        "strict replay: {message}; supply the original bytes, or use cli-bench run with a new experiment ID for changed resources"
    ))
}
#[cfg(test)]
mod replay_tests {
    use super::*;
    use crate::test_support::{equal, require, validation_fixture};
    use crate::*;
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    #[test]
    fn strict_replay_preserves_contract_and_rejects_changed_bindings() -> TestResult {
        let fixture = validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let prepared = fixture.prepare(MeasurementProfile::Smoke)?;
        let bindings = prepared.roles().clone();
        let executable = bindings.generator.as_ref().ok_or("generator")?;
        let harness = BoundTool {
            path: executable.path.clone(),
            identity: ToolIdentity {
                file: executable.artifact.file.clone(),
                version: "fixture harness".into(),
            },
        };
        let host = collect_host(&fixture.suite.environment);
        let original = run(
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
        equal(&original.result.outcome, &RunOutcome::Complete)?;
        let recipe = replay_request(&original, &bindings)?;
        let replayed = recipe.execute(
            &ReplayContext {
                measurement_lock: &fixture.measurement_lock,
                harness: &harness,
                host: &host,
                mode: RunMode::CheckOnly { engine: None },
                cache_root: &fixture.request.cache_root,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        equal(&replayed.result.outcome, &RunOutcome::Complete)?;
        equal(&original.manifest.contract, &replayed.manifest.contract)?;
        require(
            original.manifest.run_id != replayed.manifest.run_id,
            "reused run ID",
        )?;
        let mut changed = bindings.clone();
        changed
            .roles
            .get_mut(&Role::Candidate)
            .ok_or("candidate")?
            .artifact
            .file
            .sha256 = "0".repeat(64);
        require(
            replay_request(&original, &changed).is_err(),
            "accepted changed binary",
        )?;
        Ok(())
    }
    fn retain_candidate_build(
        fixture: &mut crate::test_support::validation_support::Fixture,
    ) -> TestResult {
        let ExecutableSource::Prebuilt(path) = &fixture.request.candidate else {
            return Err("candidate".into());
        };
        let provenance = BuildRecord {
            source_sha: "a".repeat(40),
            lockfile: fingerprint(path)?,
            policy: ResolvedBuildPolicy {
                compiler: "fixture compiler".into(),
                cargo: "fixture cargo".into(),
                target: "fixture-target".into(),
                settings: fixture.suite.build.clone(),
                cargo_config_hashes: vec![],
                environment_hash: None,
            },
            command: vec![
                "cargo".into(),
                "build".into(),
                "--release".into(),
                "--locked".into(),
            ],
            resolved_features: vec![],
            evidence: None,
        };
        let artifact = register_binary(path, Some(provenance), &fixture.store)?;
        fixture.request.candidate = ExecutableSource::Retained(Box::new(BoundExecutable {
            path: path.clone(),
            artifact,
        }));
        let Some(ExecutableSource::Prebuilt(generator)) = &fixture.request.generator else {
            return Err("generator".into());
        };
        let build = match &fixture.request.candidate {
            ExecutableSource::Retained(bound) => bound.artifact.build.clone(),
            _ => None,
        };
        let artifact = register_binary(generator, build, &fixture.store)?;
        fixture.request.generator = Some(ExecutableSource::Retained(Box::new(BoundExecutable {
            path: generator.clone(),
            artifact,
        })));
        Ok(())
    }
    #[test]
    fn smoke_measurement_replay_keeps_rss_tools_and_normalized_observations() -> TestResult {
        let fixture = validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let engine = crate::test_support::timing_support::engine(&fixture, "")?;
        let time = crate::test_support::rss_support::time_tool(&fixture, Platform::Linux, "")?;
        let host = collect_host(&fixture.suite.environment);
        let original = run(
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
                harness: &engine,
                host: &host,
                mode: RunMode::Measure {
                    engine: &engine,
                    time: Some(&time),
                    platform: Platform::Linux,
                },
                experiment: None,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        equal(&original.result.outcome, &RunOutcome::Complete)?;
        let path = fixture.root.join("portable-measured");
        export_bundle(
            &original,
            &ExportRequest {
                destination: path.clone(),
                with_inputs: false,
                with_binaries: true,
            },
        )?;
        let portable = load_bundle(&path)?;
        let bindings = replay_bindings(&portable, &fixture.request.home, &fixture.request.config)?;
        let tools = replay_tools(&portable)?;
        let engine = tools.engine.as_ref().ok_or("engine")?;
        let time = tools.time.as_ref().ok_or("time")?;
        let recipe = replay_request(&portable, &bindings)?;
        let context = ReplayContext {
            measurement_lock: &fixture.measurement_lock,
            harness: &tools.harness,
            host: &host,
            mode: RunMode::Measure {
                engine,
                time: Some(time),
                platform: Platform::Linux,
            },
            cache_root: &fixture.request.cache_root,
        };
        let replayed = recipe.execute(&context, &fixture.store, &fixture.runner)?;
        equal(&replayed.result.outcome, &RunOutcome::Complete)?;
        equal(&replayed.manifest.contract, &original.manifest.contract)?;
        let record = publication_record(&replayed)?;
        equal(&record.analysis.timing_samples.len(), &8)?;
        equal(&record.analysis.rss_samples.len(), &2)?;
        require(record.analysis.smoke_only, "replay promoted smoke")?;
        let history = crate::history_record(&portable)?;
        let mut normalized = publication_record(&portable)?;
        normalized.replay.clone_from(&history.publication.replay);
        let mut report = Vec::new();
        let mut compact_report = Vec::new();
        render_record(&normalized, ReportFormat::Json, &mut report)?;
        render_record(
            &history.publication,
            ReportFormat::Json,
            &mut compact_report,
        )?;
        equal(&report, &compact_report)?;
        Ok(())
    }
    #[test]
    fn saved_kind_and_tools_are_frozen_before_any_replay_workload() -> TestResult {
        let fixture = validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let engine = crate::test_support::timing_support::engine(&fixture, "")?;
        let host = collect_host(&fixture.suite.environment);
        let original = run(
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
                harness: &engine,
                host: &host,
                mode: RunMode::CheckOnly {
                    engine: Some(&engine),
                },
                experiment: None,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        equal(&original.result.outcome, &RunOutcome::Complete)?;
        let bindings = replay_bindings(&original, &fixture.request.home, &fixture.request.config)?;
        let recipe = replay_request(&original, &bindings)?;
        let mut context = ReplayContext {
            measurement_lock: &fixture.measurement_lock,
            harness: &engine,
            host: &host,
            mode: RunMode::Measure {
                engine: &engine,
                time: None,
                platform: Platform::Linux,
            },
            cache_root: &fixture.request.cache_root,
        };
        let before = fs::read_dir(fixture.store.root().join("runs"))?.count();
        require(
            recipe
                .execute(&context, &fixture.store, &fixture.runner)
                .is_err(),
            "inferred measure from engine identity",
        )?;
        context.mode = RunMode::CheckOnly { engine: None };
        require(
            recipe
                .execute(&context, &fixture.store, &fixture.runner)
                .is_err(),
            "discarded saved engine identity",
        )?;
        equal(
            &before,
            &fs::read_dir(fixture.store.root().join("runs"))?.count(),
        )?;
        context.mode = RunMode::CheckOnly {
            engine: Some(&engine),
        };
        let replayed = recipe.execute(&context, &fixture.store, &fixture.runner)?;
        equal(&replayed.result.outcome, &RunOutcome::Complete)?;
        equal(&replayed.manifest.contract, &original.manifest.contract)?;
        require(
            publication_record(&replayed)?
                .analysis
                .timing_samples
                .is_empty(),
            "tagged check measured",
        )?;
        let mut changed = engine.clone();
        changed.identity.version = "different harness".into();
        context.harness = &changed;
        require(
            recipe
                .execute(&context, &fixture.store, &fixture.runner)
                .is_err(),
            "changed harness accepted",
        )?;
        Ok(())
    }
    #[test]
    fn portable_resources_are_optional_and_replay_preserves_built_provenance() -> TestResult {
        let mut fixture = validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        retain_candidate_build(&mut fixture)?;
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
        let original = run(
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
        equal(&original.result.outcome, &RunOutcome::Complete)?;
        let compact_path = fixture.root.join("compact");
        let compact = export_bundle(
            &original,
            &ExportRequest {
                destination: compact_path.clone(),
                with_inputs: false,
                with_binaries: false,
            },
        )?;
        require(
            compact.resources.values().all(|r| r.path.is_none()),
            "unrequested resource bytes copied",
        )?;
        let compact_bundle = load_bundle(&compact_path)?;
        let missing = replay_bindings(
            &compact_bundle,
            &fixture.request.home,
            &fixture.request.config,
        )?;
        require(
            replay_request(&compact_bundle, &missing).is_err(),
            "missing bindings accepted",
        )?;
        let portable_path = fixture.root.join("portable");
        let index = export_bundle(
            &original,
            &ExportRequest {
                destination: portable_path.clone(),
                with_inputs: true,
                with_binaries: true,
            },
        )?;
        require(
            index.resources.values().all(|r| r.path.is_some()),
            "requested bytes omitted",
        )?;
        let expected_history = crate::history_record(&original)?;
        let portable = load_bundle(&portable_path)?;
        fs::remove_dir_all(fixture.store.root())?;
        let bindings = replay_bindings(&portable, &fixture.request.home, &fixture.request.config)?;
        let recipe = replay_request(&portable, &bindings)?;
        let tools = replay_tools(&portable)?;
        let store = Store::open(&fixture.root.join("new-store"))?;
        let replayed = recipe.execute(
            &ReplayContext {
                measurement_lock: &fixture.measurement_lock,
                harness: &tools.harness,
                host: &host,
                mode: RunMode::CheckOnly { engine: None },
                cache_root: &fixture.request.cache_root,
            },
            &store,
            &fixture.runner,
        )?;
        equal(&replayed.result.outcome, &RunOutcome::Complete)?;
        equal(&replayed.manifest.roles, &original.manifest.roles)?;
        equal(&replayed.manifest.contract, &original.manifest.contract)?;
        let actual_history = crate::history_record(&portable)?;
        equal(
            &serde_json::to_vec(&actual_history)?,
            &serde_json::to_vec(&expected_history)?,
        )?;
        Ok(())
    }
    #[test]
    fn changed_biggie_output_fails_without_refreshing_expected_hashes() -> TestResult {
        let mut fixture = validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let marker = fs::canonicalize(fixture.root.path())?.join("change-output");
        let generator_path = crate::test_support::validation_support::script(
            fixture.root.path(),
            "conditional-generator",
            &format!(
                "for output do :; done\nif [ -e '{}' ]; then printf 'IJKL\\nEFGH\\n'; else printf 'ABCD\\nEFGH\\n'; fi > \"$output\"",
                marker.display()
            ),
        )?;
        fixture.request.generator = Some(ExecutableSource::Prebuilt(generator_path));
        let prepared = fixture.prepare(MeasurementProfile::Smoke)?;
        let bindings = prepared.roles().clone();
        let generator = bindings.generator.as_ref().ok_or("generator")?;
        let harness = BoundTool {
            path: generator.path.clone(),
            identity: ToolIdentity {
                file: generator.artifact.file.clone(),
                version: "fixture harness".into(),
            },
        };
        let host = collect_host(&fixture.suite.environment);
        let original = run(
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
        equal(&original.result.outcome, &RunOutcome::Complete)?;
        let recipe = replay_request(&original, &bindings)?;
        let store = Store::open(&fixture.root.join("new-store"))?;
        fs::write(marker, "change")?;
        let replayed = recipe.execute(
            &ReplayContext {
                measurement_lock: &fixture.measurement_lock,
                harness: &harness,
                host: &host,
                mode: RunMode::CheckOnly { engine: None },
                cache_root: &fixture.request.cache_root,
            },
            &store,
            &fixture.runner,
        )?;
        equal(&replayed.result.outcome, &RunOutcome::Failed)?;
        require(
            replayed
                .result
                .message
                .as_ref()
                .is_some_and(|m| m.contains("identity") || m.contains("hash")),
            "missing changed output diagnostic",
        )?;
        equal(
            &load_bundle(&original.path)?.manifest.inputs,
            &original.manifest.inputs,
        )?;
        Ok(())
    }
}
