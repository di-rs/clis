//! Isolated Cargo builds and explicit executable bindings.

use crate::BenchError;
use std::{io::BufRead, path::PathBuf};

use crate::{ArtifactRecord, BoundTool, BuildPolicy, ProcessRunner, RoleBindings, Store, Suite};
use std::{collections::BTreeMap, path::Path};

/// Selected Git executable, child environment and caller-owned capture/snapshot root.
#[derive(Clone, Debug)]
pub struct GitContext {
    pub tool: BoundTool,
    pub environment: BTreeMap<String, String>,
    pub scratch_root: PathBuf,
}
/// Immutable commit selection. Dirty edits are observed but always excluded.
#[derive(Clone, Debug)]
pub struct ResolvedRevision {
    pub requested: String,
    pub sha: String,
    pub dirty: bool,
}
/// One frozen compiler/Cargo pair and explicit child settings for every Rust role.
#[derive(Clone, Debug)]
pub struct BuildTools {
    pub cargo: BoundTool,
    pub rustc: BoundTool,
    pub environment: BTreeMap<String, String>,
}
/// Isolated release/locked build. None binary requires a sole binary in the package.
#[derive(Clone, Debug)]
pub struct BuildRequest {
    pub repository: PathBuf,
    pub revision: ResolvedRevision,
    pub package: String,
    pub binary: Option<String>,
    pub policy: BuildPolicy,
    pub git: GitContext,
    pub tools: BuildTools,
    pub cache_root: PathBuf,
}
/// Explicit source revision or prebuilt executable with unknown provenance.
#[derive(Clone, Debug)]
pub enum ExecutableSource {
    Revision(String),
    Prebuilt(PathBuf),
}
/// Role selection and caller-owned execution directories. No ambient tool discovery.
#[derive(Clone, Debug)]
pub struct RoleRequest {
    pub repository: PathBuf,
    pub candidate: ExecutableSource,
    pub previous: Option<ExecutableSource>,
    pub reference: Option<PathBuf>,
    pub generator: Option<ExecutableSource>,
    pub git: Option<GitContext>,
    pub tools: Option<BuildTools>,
    pub cache_root: PathBuf,
    pub home: PathBuf,
    pub config: PathBuf,
    pub pipeline: Option<crate::PipelineTools>,
}

/// Resolve a Git ref once, without changing the active checkout.
/// Requires the session guard before scratch allocation or repository scanning.
///
/// ```compile_fail
/// use cli_bench::{GitContext, ProcessRunner, resolve_revision};
/// fn resolve_without_session(repository: &std::path::Path, git: &GitContext, runner: &ProcessRunner) {
///     let _ = resolve_revision(repository, "HEAD", git, runner);
/// }
/// ```
/// # Errors
/// Rejects invalid references, changed Git identity and failed bounded Git commands.
pub fn resolve_revision(
    _measurement_lock: &crate::MeasurementLock,
    repository: &Path,
    revision: &str,
    git: &GitContext,
    runner: &ProcessRunner,
) -> Result<ResolvedRevision, BenchError> {
    validate_revision(revision)?;
    let repository = std::fs::canonicalize(repository)?;
    let work = OwnedDirectory::new(&git.scratch_root, "resolve")?;
    let sha = git_command(
        git,
        runner,
        &repository,
        &work.path,
        &[
            "rev-parse",
            "--verify",
            "--end-of-options",
            &format!("{revision}^{{commit}}"),
        ],
    )?;
    let sha = sha.trim().to_owned();
    validate_sha(&sha)?;
    let status = git_command(
        git,
        runner,
        &repository,
        &work.path,
        &["status", "--porcelain", "--untracked-files=normal"],
    )?;
    Ok(ResolvedRevision {
        requested: revision.into(),
        sha,
        dirty: !status.is_empty(),
    })
}
/// Build an exact committed source snapshot and retain its verified executable.
/// Uses memory bounded by the runner's stream cap for Cargo metadata; artifact JSON is streamed.
/// # Errors
/// Rejects unsupported source, unavailable tools, policy drift and invalid Cargo evidence.
pub fn build_revision(
    measurement_lock: &crate::MeasurementLock,
    request: &BuildRequest,
    store: &Store,
    runner: &ProcessRunner,
) -> Result<ArtifactRecord, BenchError> {
    validate_request(request)?;
    let repository = std::fs::canonicalize(&request.repository)?;
    let scratch = std::fs::canonicalize(&request.git.scratch_root)?;
    if scratch.starts_with(&repository) {
        return Err(build_error(
            "source scratch root must be outside the active repository to exclude its Cargo configuration",
        ));
    }
    std::fs::create_dir_all(&request.cache_root)?;
    let cache_root = std::fs::canonicalize(&request.cache_root)?;
    let work = OwnedDirectory::new(&scratch, "source")?;
    let logs = crate::store::unique_directory(&cache_root, "build")?;
    crate::store::atomic_json(
        &logs.join("measurement-lock.json"),
        &serde_json::json!({"wait_seconds": measurement_lock.wait_duration().as_secs_f64()}),
    )?;
    verify_tools(&request.tools, runner, &work.path, &logs)?;
    let source = create_snapshot(request, runner, &repository, &work.path, &logs)?;
    let lock_path = source.join("Cargo.lock");
    let lockfile = crate::fingerprint(&lock_path)?;
    let target = logs.join("target");
    std::fs::create_dir(&target)?;
    let environment = build_environment(request, &target)?;
    let configurations = cargo_configurations(&source, &environment)?;
    let metadata_args = cargo_arguments(request, "metadata", None)?;
    let metadata_text = run_text(
        &request.tools.cargo.path,
        &metadata_args,
        &source,
        &environment,
        &logs,
        runner,
    )?;
    let metadata: serde_json::Value = serde_json::from_str(&metadata_text)?;
    let package = select_package(&metadata, &request.package)?;
    let package_id = json_string(package, "id")?;
    let binary = select_binary(package, request.binary.as_deref())?;
    let dependencies = dependency_evidence(&metadata, &source)?;
    let mut policy = resolved_policy(request, &configurations)?;
    // The ephemeral target path is not a build setting. All supplied environment
    // effective settings are hashed, so credentials cannot leak into provenance.
    let environment_hash = environment_identity(request, &environment)?;
    policy.environment_hash = Some(environment_hash.clone());
    let key = crate::artifact::json_identity(&(
        &request.revision.sha,
        &lockfile,
        &policy,
        &request.package,
        &binary,
        &dependencies,
        &environment_hash,
        store.root(),
    ))?;
    let index = cache_root.join(format!("{key}.json"));
    verify_unchanged(&[(lock_path.clone(), lockfile.clone())])?;
    verify_configurations(&source, &environment, &configurations)?;
    let provenance = BuildProvenance {
        binary,
        dependencies,
        environment_hash,
        lockfile,
        policy,
    };
    if index.exists() {
        let cached = load_cached_build(&index, request, &provenance, store)?;
        verify_tools(&request.tools, runner, &source, &logs)?;
        return Ok(cached);
    }
    let arguments = cargo_arguments(request, "build", Some(&provenance.binary))?;
    let output = run_capture(
        &request.tools.cargo.path,
        &arguments,
        &source,
        &environment,
        &logs,
        runner,
    )?;
    let selected = parse_artifacts(
        std::io::BufReader::new(std::fs::File::open(output)?),
        package_id,
        &provenance.binary,
    )?;
    validate_executable(&selected.executable, &target)?;
    verify_unchanged(&[(lock_path, provenance.lockfile.clone())])?;
    verify_configurations(&source, &environment, &configurations)?;
    verify_tools(&request.tools, runner, &source, &logs)?;
    git_command(
        &request.git,
        runner,
        &source,
        &logs,
        &["diff", "--exit-code", "HEAD", "--"],
    )?;
    let record = retain_build(request, store, selected, package_id, provenance, arguments)?;
    crate::store::atomic_json(&index, &record)?;
    Ok(record)
}

fn load_cached_build(
    index: &Path,
    request: &BuildRequest,
    provenance: &BuildProvenance,
    store: &Store,
) -> Result<ArtifactRecord, BenchError> {
    let cached: ArtifactRecord = serde_json::from_slice(&std::fs::read(index)?)?;
    store.verify_artifact(&cached)?;
    let build = cached
        .build
        .as_ref()
        .ok_or_else(|| build_error("cached build has unknown provenance"))?;
    if build.source_sha != request.revision.sha
        || build.lockfile != provenance.lockfile
        || build.policy != provenance.policy
    {
        return Err(build_error("cached build policy mismatch"));
    }
    let evidence = build
        .evidence
        .as_ref()
        .ok_or_else(|| build_error("cached build omitted Cargo evidence"))?;
    if evidence.package != request.package
        || evidence.binary != provenance.binary
        || evidence.dependencies != provenance.dependencies
        || evidence.environment_hash != provenance.environment_hash
    {
        return Err(build_error(
            "cached Cargo evidence differs from complete build policy",
        ));
    }
    check_program(&store.artifact_path(&cached))?;
    Ok(cached)
}

fn create_snapshot(
    request: &BuildRequest,
    runner: &ProcessRunner,
    repository: &Path,
    work: &Path,
    logs: &Path,
) -> Result<PathBuf, BenchError> {
    let source = work.join("checkout");
    git_command(
        &request.git,
        runner,
        repository,
        logs,
        &[
            "clone",
            "--local",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            path_text(repository)?,
            path_text(&source)?,
        ],
    )?;
    let tree = git_command(
        &request.git,
        runner,
        &source,
        logs,
        &["ls-tree", "-r", &request.revision.sha],
    )?;
    if tree.lines().any(|line| line.starts_with("160000 ")) {
        return Err(build_error(
            "submodule sources are unsupported; supply an explicitly built executable",
        ));
    }
    git_command(
        &request.git,
        runner,
        &source,
        logs,
        &[
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--detach",
            &request.revision.sha,
            "--",
        ],
    )?;
    reject_lfs(&source)?;
    Ok(source)
}

fn build_environment(
    request: &BuildRequest,
    target: &Path,
) -> Result<BTreeMap<String, String>, BenchError> {
    let mut environment = request.tools.environment.clone();
    environment.insert("RUSTC".into(), path_text(&request.tools.rustc.path)?.into());
    environment.insert(
        "CARGO_ENCODED_RUSTFLAGS".into(),
        request.policy.rustflags.join("\u{1f}"),
    );
    environment.insert(
        "CARGO_PROFILE_RELEASE_STRIP".into(),
        match request.policy.strip {
            crate::StripPolicy::None => "none",
            crate::StripPolicy::Debuginfo => "debuginfo",
            crate::StripPolicy::Symbols => "symbols",
        }
        .into(),
    );
    environment.insert("CARGO_TARGET_DIR".into(), path_text(target)?.into());
    // Prevent any per-revision rustup override or automatic tool installation.
    environment.insert("RUSTUP_AUTO_INSTALL".into(), "0".into());
    controlled_environment(&mut environment, target)?;
    Ok(environment)
}

fn environment_identity(
    request: &BuildRequest,
    environment: &BTreeMap<String, String>,
) -> Result<String, BenchError> {
    let mut comparable = environment.clone();
    comparable.insert("CARGO_TARGET_DIR".into(), "[owned target]".into());
    comparable.insert("RUSTC".into(), "[selected compiler]".into());
    if !request.tools.environment.contains_key("HOME") {
        comparable.insert("HOME".into(), "[owned home]".into());
        if !request.tools.environment.contains_key("CARGO_HOME") {
            comparable.insert("CARGO_HOME".into(), "[owned cargo home]".into());
        }
    }
    crate::artifact::json_identity(&comparable)
}

fn selected_target(request: &BuildRequest) -> Result<String, BenchError> {
    let target_name = request
        .policy
        .target
        .clone()
        .or_else(|| {
            request
                .tools
                .rustc
                .identity
                .version
                .lines()
                .find_map(|line| line.strip_prefix("host: ").map(str::to_owned))
        })
        .ok_or_else(|| build_error("compiler did not identify its host target"))?;
    Ok(target_name)
}

fn resolved_policy(
    request: &BuildRequest,
    configurations: &[(PathBuf, crate::FileIdentity)],
) -> Result<crate::ResolvedBuildPolicy, BenchError> {
    let target_name = selected_target(request)?;
    let policy = crate::ResolvedBuildPolicy {
        compiler: tool_description(&request.tools.rustc),
        cargo: tool_description(&request.tools.cargo),
        target: target_name,
        settings: request.policy.clone(),
        environment_hash: None,
        cargo_config_hashes: configurations
            .iter()
            .map(|(_, identity)| identity.clone())
            .collect(),
    };
    Ok(policy)
}

struct BuildProvenance {
    binary: String,
    dependencies: Vec<crate::CargoDependency>,
    environment_hash: String,
    lockfile: crate::FileIdentity,
    policy: crate::ResolvedBuildPolicy,
}
fn retain_build(
    request: &BuildRequest,
    store: &Store,
    selected: SelectedArtifact,
    package_id: &str,
    provenance: BuildProvenance,
    arguments: Vec<String>,
) -> Result<ArtifactRecord, BenchError> {
    let BuildProvenance {
        binary,
        dependencies,
        environment_hash,
        lockfile,
        policy,
    } = provenance;
    let evidence = crate::CargoEvidence {
        artifact: serde_json::json!({
            "reason":"compiler-artifact", "package_id_hash":crate::artifact::json_identity(&package_id)?,
            "target":{"name":binary,"kind":["bin"], "crate_types":selected.record.pointer("/target/crate_types"), "edition":selected.record.pointer("/target/edition")},
            "profile":selected.record.get("profile"), "fresh":selected.record.get("fresh"),
            "features":selected.features, "executable": "[retained executable]"
        }),
        package: request.package.clone(),
        binary,
        dependencies,
        environment_hash,
    };
    let build = crate::BuildRecord {
        source_sha: request.revision.sha.clone(),
        lockfile,
        policy,
        command: std::iter::once("cargo".into()).chain(arguments).collect(),
        resolved_features: selected.features,
        evidence: Some(evidence),
    };
    let record = crate::register_binary(&selected.executable, Some(build), store)?;
    Ok(record)
}

/// Resolve all requested roles and generator using one selected Rust toolchain.
/// # Errors
/// Rejects missing comparators/tools, unsupported source or changed identities.
pub fn bind_roles(
    measurement_lock: &crate::MeasurementLock,
    request: &RoleRequest,
    suite: &Suite,
    store: &Store,
    runner: &ProcessRunner,
) -> Result<RoleBindings, BenchError> {
    crate::validate_suite(suite)?;
    if request.previous.is_none() && request.reference.is_none() {
        return Err(build_error(
            "comparison requires an explicit previous or reference executable",
        ));
    }
    for path in [&request.home, &request.config] {
        path_text(path)?;
        if !std::fs::symlink_metadata(path)?.is_dir() {
            return Err(build_error(
                "role HOME/config must be caller-owned directories",
            ));
        }
    }
    if let Some(reference) = &request.reference {
        check_program(reference)?;
    }
    let candidate = prepare_source(measurement_lock, &request.candidate, request, runner)?;
    let previous = request
        .previous
        .as_ref()
        .map(|source| prepare_source(measurement_lock, source, request, runner))
        .transpose()?;
    let generator_source = request
        .generator
        .clone()
        .unwrap_or_else(|| ExecutableSource::Revision(suite.generator.revision.clone()));
    let generator = prepare_source(measurement_lock, &generator_source, request, runner)?;
    let mut roles = BTreeMap::new();
    for (role, selection) in [
        (crate::Role::Candidate, Some(candidate)),
        (crate::Role::Previous, previous),
    ] {
        if let Some(selection) = selection {
            roles.insert(
                role,
                bind_source(
                    measurement_lock,
                    selection,
                    request,
                    (&suite.package, &suite.binary),
                    &suite.build,
                    store,
                    runner,
                )?,
            );
        }
    }
    if let Some(path) = &request.reference {
        roles.insert(crate::Role::Reference, retain_prebuilt(path, store)?);
    }
    let mut generator_policy = suite.build.clone();
    generator_policy
        .features
        .clone_from(&suite.generator.features);
    generator_policy.no_default_features = suite.generator.no_default_features;
    let generator = bind_source(
        measurement_lock,
        generator,
        request,
        (&suite.generator.package, &suite.generator.binary),
        &generator_policy,
        store,
        runner,
    )?;
    Ok(RoleBindings {
        roles,
        generator: Some(generator),
        environment: suite.environment.clone(),
        home: request.home.clone(),
        config: request.config.clone(),
        pipeline: request.pipeline.clone(),
    })
}
enum PreparedSource {
    Revision(ResolvedRevision),
    Prebuilt(PathBuf),
}
fn prepare_source(
    measurement_lock: &crate::MeasurementLock,
    source: &ExecutableSource,
    request: &RoleRequest,
    runner: &ProcessRunner,
) -> Result<PreparedSource, BenchError> {
    match source {
        ExecutableSource::Prebuilt(path) => {
            check_program(path)?;
            Ok(PreparedSource::Prebuilt(path.clone()))
        }
        ExecutableSource::Revision(revision) => {
            let git = request.git.as_ref().ok_or_else(|| {
                build_error("requested source build requires explicit Git context")
            })?;
            if request.tools.is_none() {
                return Err(build_error(
                    "requested source build requires selected Cargo/compiler",
                ));
            }
            Ok(PreparedSource::Revision(resolve_revision(
                measurement_lock,
                &request.repository,
                revision,
                git,
                runner,
            )?))
        }
    }
}
fn bind_source(
    measurement_lock: &crate::MeasurementLock,
    source: PreparedSource,
    request: &RoleRequest,
    target: (&str, &str),
    policy: &BuildPolicy,
    store: &Store,
    runner: &ProcessRunner,
) -> Result<crate::BoundExecutable, BenchError> {
    let artifact = match source {
        PreparedSource::Prebuilt(path) => return retain_prebuilt(&path, store),
        PreparedSource::Revision(revision) => build_revision(
            measurement_lock,
            &BuildRequest {
                repository: request.repository.clone(),
                revision,
                package: target.0.into(),
                binary: Some(target.1.into()),
                policy: policy.clone(),
                git: request
                    .git
                    .clone()
                    .ok_or_else(|| build_error("missing Git context"))?,
                tools: request
                    .tools
                    .clone()
                    .ok_or_else(|| build_error("missing build tools"))?,
                cache_root: request.cache_root.clone(),
            },
            store,
            runner,
        )?,
    };
    Ok(crate::BoundExecutable {
        path: store.artifact_path(&artifact),
        artifact,
    })
}
fn retain_prebuilt(path: &Path, store: &Store) -> Result<crate::BoundExecutable, BenchError> {
    check_program(path)?;
    let artifact = crate::register_binary(path, None, store)?;
    Ok(crate::BoundExecutable {
        path: store.artifact_path(&artifact),
        artifact,
    })
}
fn check_program(path: &Path) -> Result<(), BenchError> {
    use std::os::unix::fs::PermissionsExt;
    path_text(path)?;
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
        return Err(build_error(
            "requested executable must be a regular executable file",
        ));
    }
    Ok(())
}

fn build_error(message: impl Into<String>) -> BenchError {
    BenchError::Evidence(message.into())
}
fn path_text(path: &Path) -> Result<&str, BenchError> {
    crate::process::utf8_path(path)
}
fn validate_sha(sha: &str) -> Result<(), BenchError> {
    if sha.len() != 40 || !sha.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(build_error("expected immutable full commit SHA"));
    }
    Ok(())
}
struct OwnedDirectory {
    path: PathBuf,
}
impl OwnedDirectory {
    fn new(parent: &Path, prefix: &str) -> Result<Self, BenchError> {
        path_text(parent)?;
        Ok(Self {
            path: crate::store::unique_directory(parent, prefix)?,
        })
    }
}
impl Drop for OwnedDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}
fn run_capture(
    program: &Path,
    arguments: &[String],
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    logs: &Path,
    runner: &ProcessRunner,
) -> Result<PathBuf, BenchError> {
    let capture = crate::store::unique_directory(logs, "command")?;
    let paths = crate::CapturePaths {
        stdout: capture.join("stdout"),
        stderr: capture.join("stderr"),
    };
    let result = runner.execute(
        &crate::CommandSpec {
            program: program.into(),
            argv: arguments.into(),
            cwd: cwd.into(),
            environment: environment.clone(),
            stdin: crate::CommandInput::Null,
            stdout: crate::CommandOutput::Capture,
        },
        &paths,
    )?;
    result.check_expected(0).map_err(|error| {
        BenchError::Execution(format!(
            "{}: {error}; diagnostics: {}",
            program.display(),
            paths.stderr.display()
        ))
    })?;
    Ok(paths.stdout)
}
fn run_text(
    program: &Path,
    arguments: &[String],
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    logs: &Path,
    runner: &ProcessRunner,
) -> Result<String, BenchError> {
    Ok(std::fs::read_to_string(run_capture(
        program,
        arguments,
        cwd,
        environment,
        logs,
        runner,
    )?)?)
}
fn git_environment(supplied: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    supplied
        .iter()
        .filter(|(key, _)| {
            matches!(
                key.as_str(),
                "PATH" | "HOME" | "TMPDIR" | "LC_ALL" | "LANG" | "TZ"
            )
        })
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}
fn controlled_environment(
    environment: &mut BTreeMap<String, String>,
    target: &Path,
) -> Result<(), BenchError> {
    for name in ["RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"] {
        environment.insert(name.into(), String::new());
    }
    let home = environment
        .entry("HOME".into())
        .or_insert(path_text(&target.join("home"))?.into())
        .clone();
    environment
        .entry("CARGO_HOME".into())
        .or_insert(path_text(&Path::new(&home).join(".cargo"))?.into());
    for name in ["HOME", "CARGO_HOME", "RUSTUP_HOME"] {
        if let Some(value) = environment.get(name) {
            path_text(Path::new(value))?;
        }
    }
    std::fs::create_dir_all(home)?;
    Ok(())
}

fn git_command(
    git: &GitContext,
    runner: &ProcessRunner,
    cwd: &Path,
    logs: &Path,
    args: &[&str],
) -> Result<String, BenchError> {
    crate::verify_file(&git.tool.path, &git.tool.identity.file)?;
    let mut environment = git_environment(&git.environment);
    environment.insert("GIT_CONFIG_NOSYSTEM".into(), "1".into());
    environment.insert("GIT_CONFIG_GLOBAL".into(), "/dev/null".into());
    environment.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    environment.insert("GIT_OPTIONAL_LOCKS".into(), "0".into());
    let output = run_text(
        &git.tool.path,
        &args.iter().map(|arg| (*arg).into()).collect::<Vec<_>>(),
        cwd,
        &environment,
        logs,
        runner,
    )?;
    crate::verify_file(&git.tool.path, &git.tool.identity.file)?;
    Ok(output)
}
fn verify_tools(
    tools: &BuildTools,
    runner: &ProcessRunner,
    cwd: &Path,
    logs: &Path,
) -> Result<(), BenchError> {
    for tool in [&tools.cargo, &tools.rustc] {
        crate::verify_file(&tool.path, &tool.identity.file)?;
        let version = run_text(
            &tool.path,
            &["-Vv".into()],
            cwd,
            &tools.environment,
            logs,
            runner,
        )?;
        if version.trim() != tool.identity.version.trim() {
            return Err(build_error("selected compiler/Cargo identity changed"));
        }
        crate::verify_file(&tool.path, &tool.identity.file)?;
    }
    Ok(())
}
fn tool_description(tool: &BoundTool) -> String {
    format!(
        "{}\nbinary-sha256: {}",
        tool.identity.version.trim(),
        tool.identity.file.sha256
    )
}
fn validate_request(request: &BuildRequest) -> Result<(), BenchError> {
    validate_sha(&request.revision.sha)?;
    for value in std::iter::once(&request.package)
        .chain(request.binary.iter())
        .chain(request.policy.toolchain.iter())
        .chain(request.policy.target.iter())
        .chain(request.policy.features.iter())
    {
        validate_build_token(value)?;
    }
    if request
        .policy
        .rustflags
        .iter()
        .any(|flag| flag.chars().any(char::is_control))
    {
        return Err(build_error("Rust flags contain control characters"));
    }
    path_text(&request.repository)?;
    path_text(&request.cache_root)?;
    Ok(())
}
fn cargo_arguments(
    request: &BuildRequest,
    operation: &str,
    binary: Option<&str>,
) -> Result<Vec<String>, BenchError> {
    let mut arguments = vec![operation.into(), "--locked".into()];
    if operation == "metadata" {
        arguments.extend(["--format-version".into(), "1".into()]);
    } else {
        arguments.extend([
            "--release".into(),
            "--message-format=json".into(),
            "--package".into(),
            request.package.clone(),
        ]);
        if let Some(binary) = binary {
            arguments.extend(["--bin".into(), binary.into()]);
        }
    }
    arguments.extend([
        if operation == "metadata" {
            "--filter-platform"
        } else {
            "--target"
        }
        .into(),
        selected_target(request)?,
    ]);
    if request.policy.no_default_features {
        arguments.push("--no-default-features".into());
    }
    if !request.policy.features.is_empty() {
        arguments.extend(["--features".into(), request.policy.features.join(",")]);
    }
    Ok(arguments)
}
fn json_string<'a>(value: &'a serde_json::Value, key: &str) -> Result<&'a str, BenchError> {
    value
        .get(key)
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| build_error(format!("Cargo metadata omitted {key}")))
}
fn select_package<'a>(
    metadata: &'a serde_json::Value,
    package: &str,
) -> Result<&'a serde_json::Value, BenchError> {
    let packages = metadata
        .get("packages")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| build_error("Cargo metadata omitted packages"))?;
    let members = metadata
        .get("workspace_members")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| build_error("Cargo metadata omitted workspace members"))?;
    let mut matching = packages.iter().filter(|item| {
        item.get("name").and_then(serde_json::Value::as_str) == Some(package)
            && item.get("id").is_some_and(|id| members.contains(id))
    });
    let selected = matching
        .next()
        .ok_or_else(|| build_error("requested package is not a workspace member"))?;
    if matching.next().is_some() {
        return Err(build_error("ambiguous package selection"));
    }
    Ok(selected)
}
fn select_binary(
    package: &serde_json::Value,
    requested: Option<&str>,
) -> Result<String, BenchError> {
    let targets = package
        .get("targets")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| build_error("Cargo metadata omitted targets"))?;
    let mut binaries = targets
        .iter()
        .filter(|target| {
            target
                .get("kind")
                .and_then(serde_json::Value::as_array)
                .is_some_and(|kinds| kinds.iter().any(|kind| kind.as_str() == Some("bin")))
        })
        .filter_map(|target| target.get("name").and_then(serde_json::Value::as_str))
        .filter(|name| requested.is_none_or(|requested| *name == requested));
    let binary = binaries
        .next()
        .ok_or_else(|| build_error("requested package has no selected binary"))?;
    if binaries.next().is_some() {
        return Err(build_error(
            "package has multiple binaries; use a suite or BuildRequest with an exact binary",
        ));
    }
    Ok(binary.into())
}
fn dependency_evidence(
    metadata: &serde_json::Value,
    source: &Path,
) -> Result<Vec<crate::CargoDependency>, BenchError> {
    let packages = metadata
        .get("packages")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| build_error("Cargo metadata omitted packages"))?;
    packages
        .iter()
        .map(|package| {
            let origin = package.get("source").and_then(serde_json::Value::as_str);
            if origin.is_none()
                && !std::fs::canonicalize(json_string(package, "manifest_path")?)?
                    .starts_with(source)
            {
                return Err(build_error(
                    "path dependency escapes committed source snapshot",
                ));
            }
            Ok(crate::CargoDependency {
                name: json_string(package, "name")?.into(),
                version: json_string(package, "version")?.into(),
                source_hash: origin
                    .map(|value| crate::artifact::json_identity(&value))
                    .transpose()?,
            })
        })
        .collect()
}
fn cargo_configurations(
    source: &Path,
    environment: &BTreeMap<String, String>,
) -> Result<Vec<(PathBuf, crate::FileIdentity)>, BenchError> {
    let mut directories: Vec<PathBuf> =
        source.ancestors().map(|path| path.join(".cargo")).collect();
    if let Some(home) = environment.get("CARGO_HOME") {
        directories.push(home.into());
    } else if let Some(home) = environment.get("HOME") {
        directories.push(Path::new(home).join(".cargo"));
    }
    let mut files = Vec::new();
    for directory in directories {
        for name in ["config", "config.toml"] {
            let path = directory.join(name);
            if path.exists() {
                files.push((path.clone(), crate::fingerprint(&path)?));
            }
        }
    }
    Ok(files)
}
fn verify_configurations(
    source: &Path,
    environment: &BTreeMap<String, String>,
    expected: &[(PathBuf, crate::FileIdentity)],
) -> Result<(), BenchError> {
    if cargo_configurations(source, environment)? != expected {
        return Err(build_error("Cargo configuration changed during build"));
    }
    Ok(())
}
fn reject_lfs(root: &Path) -> Result<(), BenchError> {
    let root = std::fs::canonicalize(root)?;
    check_source_tree(&root, &root)
}
fn check_source_tree(directory: &Path, root: &Path) -> Result<(), BenchError> {
    use std::io::Read;
    for item in std::fs::read_dir(directory)? {
        let item = item?;
        if item.file_name() == ".git" {
            continue;
        }
        let kind = item.file_type()?;
        if kind.is_dir() {
            check_source_tree(&item.path(), root)?;
        } else if kind.is_symlink() {
            if !std::fs::canonicalize(item.path())?.starts_with(root) {
                return Err(build_error(
                    "committed symlink escapes isolated source snapshot",
                ));
            }
        } else if kind.is_file() {
            let mut prefix = [0_u8; 128];
            let count = std::fs::File::open(item.path())?.read(&mut prefix)?;
            if prefix.get(..count).is_some_and(|bytes| {
                bytes.starts_with(b"version https://git-lfs.github.com/spec/v1")
            }) {
                return Err(build_error(
                    "Git LFS sources are unsupported; supply an explicitly built executable",
                ));
            }
        }
    }
    Ok(())
}

fn validate_revision(revision: &str) -> Result<(), BenchError> {
    validate_build_token(revision)
}
fn validate_build_token(value: &str) -> Result<(), BenchError> {
    if value.trim().is_empty() || value.starts_with('-') || value.chars().any(char::is_control) {
        return Err(BenchError::Evidence("invalid build/revision token".into()));
    }
    Ok(())
}
struct SelectedArtifact {
    executable: PathBuf,
    features: Vec<String>,
    record: serde_json::Value,
}
fn parse_artifacts(
    input: impl BufRead,
    package: &str,
    binary: &str,
) -> Result<SelectedArtifact, BenchError> {
    let mut selected = None;
    let mut finished = false;
    for line in input.lines() {
        let message: serde_json::Value = serde_json::from_str(&line?)?;
        match message.get("reason").and_then(serde_json::Value::as_str) {
            Some("build-finished") => {
                if finished
                    || message.get("success").and_then(serde_json::Value::as_bool) != Some(true)
                {
                    return Err(BenchError::Evidence(
                        "Cargo build did not finish successfully".into(),
                    ));
                }
                finished = true;
            }
            Some("compiler-artifact")
                if message
                    .get("package_id")
                    .and_then(serde_json::Value::as_str)
                    == Some(package)
                    && message
                        .pointer("/target/name")
                        .and_then(serde_json::Value::as_str)
                        == Some(binary)
                    && message
                        .pointer("/target/kind")
                        .and_then(serde_json::Value::as_array)
                        .is_some_and(|kinds| {
                            kinds.iter().any(|kind| kind.as_str() == Some("bin"))
                        }) =>
            {
                if selected.is_some() {
                    return Err(BenchError::Evidence(
                        "ambiguous Cargo executable artifacts".into(),
                    ));
                }
                let executable = message
                    .get("executable")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| {
                        BenchError::Evidence("Cargo binary artifact omitted executable".into())
                    })?;
                let features =
                    serde_json::from_value(message.get("features").cloned().ok_or_else(|| {
                        BenchError::Evidence("Cargo artifact omitted features".into())
                    })?)?;
                selected = Some(SelectedArtifact {
                    executable: executable.into(),
                    features,
                    record: message,
                });
            }
            _ => {}
        }
    }
    if !finished {
        return Err(BenchError::Evidence(
            "Cargo omitted build-finished record".into(),
        ));
    }
    selected.ok_or_else(|| BenchError::Evidence("Cargo omitted requested binary artifact".into()))
}

fn validate_executable(path: &std::path::Path, target: &std::path::Path) -> Result<(), BenchError> {
    let relative = path.strip_prefix(target).map_err(|_| {
        BenchError::Evidence("Cargo executable escaped its owned target directory".into())
    })?;
    let mut current = target.to_path_buf();
    for component in relative.components() {
        if !matches!(component, std::path::Component::Normal(_)) {
            return Err(BenchError::Evidence("invalid Cargo executable path".into()));
        }
        current.push(component);
        if std::fs::symlink_metadata(&current)?
            .file_type()
            .is_symlink()
        {
            return Err(BenchError::Evidence(
                "symlink in Cargo executable path".into(),
            ));
        }
    }
    check_program(path)?;
    crate::fingerprint(path)?;
    Ok(())
}

fn verify_unchanged(files: &[(PathBuf, crate::FileIdentity)]) -> Result<(), BenchError> {
    files
        .iter()
        .try_for_each(|(path, expected)| crate::verify_file(path, expected))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{build as build_support, measurement_lock as test_measurement_lock};
    fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
        if condition {
            Ok(())
        } else {
            Err(message.into())
        }
    }

    #[test]
    fn rejects_option_like_or_invalid_revision_requests() {
        for revision in ["", "--help", "-cfoo=bar", "HEAD\0ignored", "HEAD\nother"] {
            assert!(
                validate_revision(revision).is_err(),
                "accepted {revision:?}"
            );
        }
        for revision in ["HEAD", "HEAD~1", "refs/heads/main", "v1.0^{commit}"] {
            assert!(validate_revision(revision).is_ok(), "rejected {revision:?}");
        }
    }

    #[test]
    fn cargo_artifact_selection_requires_exact_package_binary_and_one_executable()
    -> Result<(), Box<dyn std::error::Error>> {
        let text = concat!(
            "{\"reason\":\"compiler-artifact\",\"package_id\":\"other\",\"target\":{\"name\":\"tiny\",\"kind\":[\"bin\"]},\"executable\":\"/target/wrong\",\"features\":[]}\n",
            "{\"reason\":\"compiler-artifact\",\"package_id\":\"tiny-id\",\"target\":{\"name\":\"tiny\",\"kind\":[\"bin\"]},\"executable\":\"/target/right\",\"features\":[\"fast\"]}\n",
            "{\"reason\":\"build-finished\",\"success\":true}\n"
        );
        let selected = parse_artifacts(text.as_bytes(), "tiny-id", "tiny")?;
        require(
            selected.executable == Path::new("/target/right"),
            "selected wrong executable",
        )?;
        require(selected.features == ["fast"], "lost resolved features")?;
        require(
            parse_artifacts(text.as_bytes(), "absent", "tiny").is_err(),
            "invalid build evidence accepted",
        )?;
        require(
            parse_artifacts(text.as_bytes(), "tiny-id", "other").is_err(),
            "invalid build evidence accepted",
        )?;
        require(
            parse_artifacts(format!("{text}{text}").as_bytes(), "tiny-id", "tiny").is_err(),
            "invalid build evidence accepted",
        )?;
        for bad in [
            "",
            "not json\n",
            "{\"reason\":\"build-finished\",\"success\":false}\n",
        ] {
            require(
                parse_artifacts(bad.as_bytes(), "tiny-id", "tiny").is_err(),
                "invalid build evidence accepted",
            )?;
        }
        Ok(())
    }

    #[test]
    fn rejects_cargo_executables_outside_owned_target_or_through_symlinks()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let root = assert_fs::TempDir::new()?;
        let target = root.path().join("target");
        std::fs::create_dir(&target)?;
        let executable = target.join("tiny");
        std::fs::write(&executable, b"binary")?;
        require(
            validate_executable(&executable, &target).is_err(),
            "non-executable Cargo output accepted",
        )?;
        std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o755))?;
        validate_executable(&executable, &target)?;
        let outside = root.path().join("outside");
        std::fs::write(&outside, b"other")?;
        require(
            validate_executable(&outside, &target).is_err(),
            "invalid build evidence accepted",
        )?;
        let link = target.join("linked");
        symlink(&outside, &link)?;
        require(
            validate_executable(&link, &target).is_err(),
            "invalid build evidence accepted",
        )?;
        let linked_directory = target.join("directory");
        symlink(root.path(), &linked_directory)?;
        require(
            validate_executable(&linked_directory.join("outside"), &target).is_err(),
            "invalid build evidence accepted",
        )?;
        require(
            validate_executable(&target.join("../outside"), &target).is_err(),
            "invalid build evidence accepted",
        )?;
        require(
            validate_executable(&target.join("missing"), &target).is_err(),
            "invalid build evidence accepted",
        )?;
        Ok(())
    }

    #[test]
    fn compiler_or_lockfile_changes_fail_verification() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let compiler = root.path().join("rustc");
        let lockfile = root.path().join("Cargo.lock");
        std::fs::write(&compiler, b"compiler-v1")?;
        std::fs::write(&lockfile, b"dependency-v1")?;
        let expected_compiler = crate::fingerprint(&compiler)?;
        let expected_lockfile = crate::fingerprint(&lockfile)?;
        verify_unchanged(&[
            (compiler.clone(), expected_compiler.clone()),
            (lockfile.clone(), expected_lockfile.clone()),
        ])?;
        std::fs::write(&compiler, b"compiler-v2")?;
        require(
            verify_unchanged(&[(compiler.clone(), expected_compiler.clone())]).is_err(),
            "invalid build evidence accepted",
        )?;
        std::fs::write(&compiler, b"compiler-v1")?;
        std::fs::write(&lockfile, b"dependency-v2")?;
        require(
            verify_unchanged(&[(compiler, expected_compiler), (lockfile, expected_lockfile)])
                .is_err(),
            "invalid build evidence accepted",
        )?;
        Ok(())
    }

    #[test]
    fn child_context_cannot_redirect_git_or_select_ambient_compiler_wrappers()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let mut supplied = BTreeMap::from([
            ("PATH".into(), "/usr/bin:/bin".into()),
            ("GIT_WORK_TREE".into(), "/active/checkout".into()),
            ("GIT_DIR".into(), "/active/.git".into()),
            ("RUSTC_WRAPPER".into(), "/unselected/compiler".into()),
        ]);
        let git = git_environment(&supplied);
        require(
            !git.contains_key("GIT_DIR") && !git.contains_key("GIT_WORK_TREE"),
            "Git environment redirected checkout",
        )?;
        controlled_environment(&mut supplied, root.path())?;
        require(
            supplied.get("RUSTC_WRAPPER").is_some_and(String::is_empty),
            "ambient wrapper changed compiler",
        )?;
        require(
            supplied
                .get("HOME")
                .is_some_and(|home| Path::new(home).is_absolute()),
            "missing isolated HOME",
        )?;
        require(
            supplied
                .get("CARGO_HOME")
                .is_some_and(|home| Path::new(home).is_absolute()),
            "missing explicit Cargo home",
        )?;
        Ok(())
    }

    #[test]
    fn committed_symlinks_cannot_import_uncommitted_external_source()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::symlink;
        let root = assert_fs::TempDir::new()?;
        let source = root.path().join("source");
        std::fs::create_dir(&source)?;
        let outside = root.path().join("dirty.rs");
        std::fs::write(&outside, "fn main() {}")?;
        symlink(&outside, source.join("main.rs"))?;
        require(
            reject_lfs(&source).is_err(),
            "external source symlink accepted",
        )?;
        Ok(())
    }

    #[test]
    fn build_policy_rejects_argument_injection_and_flag_separator() {
        for value in ["", "--release", "x\0y", "a\nb", "a\u{1f}b"] {
            assert!(validate_build_token(value).is_err());
        }
        for value in [
            "tiny",
            "aarch64-apple-darwin",
            "nightly-2026-10-01",
            "dep/feature",
        ] {
            assert!(validate_build_token(value).is_ok());
        }
    }

    #[test]
    fn binds_prebuilt_roles_without_inventing_compiler_provenance_or_missing_reference()
    -> Result<(), Box<dyn std::error::Error>> {
        use crate::*;
        use std::os::unix::fs::PermissionsExt;
        let guard = test_measurement_lock()?;
        let root = assert_fs::TempDir::new()?;
        let executable = root.path().join("tool");
        std::fs::write(&executable, "#!/bin/sh\nexit 0\n")?;
        std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o755))?;
        let home = root.path().join("home");
        let config = root.path().join("config");
        std::fs::create_dir(&home)?;
        std::fs::create_dir(&config)?;
        let store = Store::open(&root.path().join("evidence"))?;
        let suite = parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let mut request = RoleRequest {
            repository: root.path().into(),
            candidate: ExecutableSource::Prebuilt(executable.clone()),
            previous: None,
            reference: Some(executable.clone()),
            generator: Some(ExecutableSource::Prebuilt(executable)),
            git: None,
            tools: None,
            cache_root: root.path().join("target/cli-bench"),
            home,
            config,
            pipeline: None,
        };
        let bindings = bind_roles(&guard, &request, &suite, &store, &build_support::runner())?;
        require(
            bindings.roles.len() == 2
                && bindings
                    .roles
                    .values()
                    .all(|role| role.artifact.build.is_none()),
            "prebuilt role was omitted or given invented compiler provenance",
        )?;
        request.reference = Some(root.path().join("missing-reference"));
        require(
            bind_roles(&guard, &request, &suite, &store, &build_support::runner()).is_err(),
            "missing explicit reference was silently ignored",
        )?;
        request.reference = None;
        require(
            bind_roles(&guard, &request, &suite, &store, &build_support::runner()).is_err(),
            "comparison without baseline was accepted",
        )?;
        Ok(())
    }

    #[test]
    fn rejects_unsupported_committed_sources_and_literal_malicious_refs()
    -> Result<(), Box<dyn std::error::Error>> {
        use crate::*;
        use build_support::{Fixture, command, runner};
        let guard = test_measurement_lock()?;
        let fixture = Fixture::new()?;
        let runner = runner();
        for revision in [
            "--help",
            "-cfoo=bar",
            "HEAD;touch marker",
            "$(touch marker)",
        ] {
            require(
                resolve_revision(&guard, &fixture.repo, revision, &fixture.git, &runner).is_err(),
                "unsafe ref was accepted",
            )?;
        }
        require(
            !fixture.repo.join("marker").exists(),
            "ref text executed as shell source",
        )?;
        let original = resolve_revision(&guard, &fixture.repo, "HEAD", &fixture.git, &runner)?;
        command(
            &fixture.repo,
            &fixture.git.tool.path,
            &[
                "update-index",
                "--add",
                "--cacheinfo",
                &format!("160000,{},module", original.sha),
            ],
        )?;
        command(
            &fixture.repo,
            &fixture.git.tool.path,
            &["commit", "--quiet", "-m", "submodule"],
        )?;
        let store = Store::open(&fixture.root.path().join("evidence"))?;
        let submodule = resolve_revision(&guard, &fixture.repo, "HEAD", &fixture.git, &runner)?;
        let error = build_revision(&guard, &fixture.request(submodule), &store, &runner)
            .err()
            .ok_or("accepted submodule")?;
        require(
            error.to_string().contains("submodule"),
            "submodule failure was not actionable",
        )?;
        command(
            &fixture.repo,
            &fixture.git.tool.path,
            &["update-index", "--force-remove", "module"],
        )?;
        std::fs::write(
            fixture.repo.join("large.dat"),
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 4\n",
        )?;
        command(&fixture.repo, &fixture.git.tool.path, &["add", "large.dat"])?;
        command(
            &fixture.repo,
            &fixture.git.tool.path,
            &["commit", "--quiet", "-m", "LFS"],
        )?;
        let lfs = resolve_revision(&guard, &fixture.repo, "HEAD", &fixture.git, &runner)?;
        let error = build_revision(&guard, &fixture.request(lfs), &store, &runner)
            .err()
            .ok_or("accepted LFS")?;
        require(
            error.to_string().contains("LFS"),
            "LFS failure was not actionable",
        )?;
        Ok(())
    }

    #[test]
    fn fake_cargo_cannot_hide_missing_artifacts_lockfile_or_compiler_changes()
    -> Result<(), Box<dyn std::error::Error>> {
        use crate::*;
        use build_support::{Fixture, runner};
        use std::os::unix::fs::PermissionsExt;
        let guard = test_measurement_lock()?;
        let fixture = Fixture::new()?;
        let runner = runner();
        let revision = resolve_revision(&guard, &fixture.repo, "HEAD", &fixture.git, &runner)?;
        let store = Store::open(&fixture.root.path().join("evidence"))?;
        for (mode, expected) in [
            ("missing", "omitted requested"),
            ("lock", "identity mismatch"),
            ("compiler", "identity mismatch"),
        ] {
            let mut request = fixture.request(revision.clone());
            let cargo = fixture.root.path().join(format!("cargo-{mode}"));
            let rustc = fixture.root.path().join(format!("rustc-{mode}"));
            let compiler_script = format!(
                "#!/bin/sh\nexec '{}' \"$@\"\n",
                fixture.tools.rustc.path.display()
            );
            std::fs::write(&rustc, compiler_script)?;
            std::fs::set_permissions(&rustc, std::fs::Permissions::from_mode(0o755))?;
            let action = match mode {
                "missing" => {
                    "printf '%s\\n' '{\"reason\":\"build-finished\",\"success\":true}'".into()
                }
                "lock" => format!(
                    "'{}' \"$@\" || exit $?\nprintf '# changed\\n' >> Cargo.lock",
                    fixture.tools.cargo.path.display()
                ),
                "compiler" => format!(
                    "'{}' \"$@\" || exit $?\nprintf '# changed\\n' >> '{}'",
                    fixture.tools.cargo.path.display(),
                    rustc.display()
                ),
                _ => return Err("invalid mode".into()),
            };
            std::fs::write(
                &cargo,
                format!(
                    "#!/bin/sh\nif [ \"$1\" = build ]; then\n{action}\nelse\nexec '{}' \"$@\"\nfi\n",
                    fixture.tools.cargo.path.display()
                ),
            )?;
            std::fs::set_permissions(&cargo, std::fs::Permissions::from_mode(0o755))?;
            request.tools.cargo.path = cargo.clone();
            request.tools.cargo.identity.file = fingerprint(&cargo)?;
            request.tools.rustc.path = rustc.clone();
            request.tools.rustc.identity.file = fingerprint(&rustc)?;
            let error = build_revision(&guard, &request, &store, &runner)
                .err()
                .ok_or("fake Cargo hid invalid build evidence")?;
            require(
                error.to_string().contains(expected),
                &format!("wrong {mode} rejection: {error}"),
            )?;
        }
        Ok(())
    }

    #[test]
    fn committed_cargo_target_config_cannot_override_the_selected_build_target()
    -> Result<(), Box<dyn std::error::Error>> {
        use crate::*;
        use build_support::{Fixture, command, runner};
        let guard = test_measurement_lock()?;
        let fixture = Fixture::new()?;
        std::fs::create_dir(fixture.repo.join(".cargo"))?;
        std::fs::write(
            fixture.repo.join(".cargo/config.toml"),
            "[build]\ntarget = 'cli-bench-nonexistent-target'\n[env]\nPRIVATE_VALUE = 'configuration-secret'\n",
        )?;
        command(&fixture.repo, &fixture.git.tool.path, &["add", ".cargo"])?;
        command(
            &fixture.repo,
            &fixture.git.tool.path,
            &["commit", "--quiet", "-m", "config"],
        )?;
        let runner = runner();
        let revision = resolve_revision(&guard, &fixture.repo, "HEAD", &fixture.git, &runner)?;
        let store = Store::open(&fixture.root.path().join("evidence"))?;
        let artifact = build_revision(&guard, &fixture.request(revision), &store, &runner)?;
        let build = artifact.build.as_ref().ok_or("missing provenance")?;
        require(
            build.policy.cargo_config_hashes.len() == 1,
            "Cargo config identity omitted",
        )?;
        require(
            !serde_json::to_string(&artifact)?.contains("configuration-secret"),
            "configuration secret published",
        )?;
        require(
            command(&fixture.repo, &store.artifact_path(&artifact), &[])? == "first",
            "recorded native target did not build",
        )?;
        Ok(())
    }
}
