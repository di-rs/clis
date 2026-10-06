use cli_bench::{Store, fingerprint, parse_suite, register_binary, verify_file};
#[test]
fn store_survives_cargo_clean() -> Result<(), Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    std::fs::write(root.path().join("Cargo.toml"), "[workspace]\nmembers=[]\n")?;
    let source = root.path().join("executable");
    std::fs::write(&source, b"retained executable")?;
    let store = Store::open(&root.path().join(".cli-bench"))?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let run = store.begin_run(&suite)?;
    let path = run.path().to_path_buf();
    let record = register_binary(&source, None, &store)?;
    let target = root.path().join("target");
    std::fs::create_dir(&target)?;
    std::fs::write(
        target.join("CACHEDIR.TAG"),
        b"Signature: 8a477f597d28d172789f06886806bc55\n",
    )?;
    std::fs::write(target.join("disposable-build"), b"cache")?;
    let mut child = std::process::Command::new("cargo")
        .args(["clean", "--offline", "--target-dir"])
        .arg(&target)
        .current_dir(root.path())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::inherit())
        .spawn()?;
    let start = std::time::Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            require(status.success(), "temporary workspace cargo clean failed")?;
            break;
        }
        if start.elapsed() > std::time::Duration::from_secs(30) {
            child.kill()?;
            child.wait()?;
            return Err("temporary workspace cargo clean timed out".into());
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    require(
        !target.exists(),
        "cargo clean did not remove disposable build cache",
    )?;
    require(
        path.join("status.json").is_file(),
        "Cargo clean removed evidence",
    )?;
    let retained = root
        .path()
        .join(".cli-bench/artifacts")
        .join(record.id)
        .join("executable");
    verify_file(&retained, &record.file)?;
    require(
        fingerprint(&source)? == record.file,
        "retained bytes changed",
    )?;
    Ok(())
}

fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}
fn resolved_manifest(
    store: &Store,
    run: &cli_bench::RunWriter,
    source: &std::path::Path,
) -> Result<cli_bench::RunManifest, Box<dyn std::error::Error>> {
    use cli_bench::*;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let tool = ToolIdentity {
        file: fingerprint(source)?,
        version: "test fixture, never executed".into(),
    };
    let policy = ResolvedBuildPolicy {
        compiler: "test compiler".into(),
        cargo: "test cargo".into(),
        target: "test target".into(),
        settings: suite.build.clone(),
        cargo_config_hashes: vec![],
        environment_hash: None,
    };
    let previous = register_binary(
        source,
        Some(BuildRecord {
            source_sha: "1111111111111111111111111111111111111111".into(),
            lockfile: tool.file.clone(),
            policy: policy.clone(),
            command: vec![
                "cargo".into(),
                "build".into(),
                "--release".into(),
                "--locked".into(),
            ],
            resolved_features: vec![],
            evidence: None,
        }),
        store,
    )?;
    let candidate = register_binary(source, None, store)?;
    Ok(RunManifest {
        schema_version: 1,
        run_id: run.id().into(),
        contract: Some(MeasurementContract {
            schema_version: 1,
            suite,
            harness: tool.clone(),
            generator: tool.clone(),
            engine: tool,
            validator_policy: "correctness-v1".into(),
            analysis_policy: "descriptive-v1".into(),
            build: policy,
            profile: MeasurementProfile::Full,
        }),
        roles: [(Role::Previous, previous), (Role::Candidate, candidate)].into(),
        inputs: vec![InputRecord {
            dataset: "tiny".into(),
            path: source.into(),
            file: fingerprint(source)?,
        }],
        selected_cases: vec!["last-line".into()],
        host: Some(collect_host(&std::collections::BTreeMap::default())),
        tool_paths: Some(ToolPaths {
            harness: source.into(),
            generator: source.into(),
            engine: source.into(),
        }),
        experiment: None,
    })
}
const fn complete() -> cli_bench::RunResult {
    cli_bench::RunResult {
        schema_version: 1,
        outcome: cli_bench::RunOutcome::Complete,
        message: None,
    }
}

#[test]
fn finalization_checks_tools_and_required_inputs() -> Result<(), Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let run = store.begin_run(&suite)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    manifest.inputs.clear();
    require(
        run.finish(&manifest, &complete()).is_err(),
        "missing required input was accepted",
    )?;
    let run = store.begin_run(&suite)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    let missing = root.path().join("absent-tool");
    if let Some(paths) = &mut manifest.tool_paths {
        paths.harness = missing;
    }
    require(
        run.finish(&manifest, &complete()).is_err(),
        "missing harness was accepted",
    )?;
    Ok(())
}

#[test]
fn experiment_retains_failed_attempts_and_rejects_input_drift()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let request = ExperimentRequest {
        id: "experiment".into(),
        hypothesis: "less work".into(),
        change_summary: "candidate change".into(),
        requested_previous: "base".into(),
        requested_candidate: "HEAD".into(),
    };
    let early = store
        .begin_tagged_run(&suite, &request)?
        .record_failure(RunOutcome::Failed, "build unavailable")?;
    require(
        early.manifest.contract.is_none() && early.manifest.roles.is_empty(),
        "invented unresolved identity",
    )?;
    require(
        !store
            .root()
            .join("experiments/experiment/experiment.json")
            .exists(),
        "early failure froze contract",
    )?;
    let run = store.begin_tagged_run(&suite, &request)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    manifest.experiment = Some(request.clone());
    let first = run.finish(&manifest, &complete())?;
    store.load_run(&first.manifest.run_id)?;
    let run = store.begin_tagged_run(&suite, &request)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    manifest.experiment = Some(request);
    let input = root.path().join("changed-input");
    std::fs::write(&input, b"changed")?;
    manifest.inputs = vec![InputRecord {
        dataset: "tiny".into(),
        path: input.clone(),
        file: fingerprint(&input)?,
    }];
    require(
        run.finish(&manifest, &complete()).is_err(),
        "same recipe silently changed input identity",
    )?;
    require(
        std::fs::read_dir(store.root().join("experiments/experiment/attempts"))?.count() == 3,
        "attempt was lost",
    )?;
    Ok(())
}

#[test]
fn finalized_bundle_detects_tampering_and_preserves_exact_suite()
-> Result<(), Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let text = include_str!("inputs/minimal-suite.toml");
    let run = store.begin_run_source(text)?;
    let manifest = resolved_manifest(&store, &run, &source)?;
    let bundle = run.finish(&manifest, &complete())?;
    store.load_run(&bundle.manifest.run_id)?;
    require(
        std::fs::read_to_string(bundle.path.join("suite.toml"))? == text,
        "submitted suite was changed",
    )?;
    std::fs::write(bundle.path.join("events.jsonl"), b"tampered")?;
    require(
        store.load_run(&bundle.manifest.run_id).is_err(),
        "tampered evidence loaded",
    )?;
    Ok(())
}

#[test]
fn experiment_allows_new_declared_selection_but_rejects_policy_or_base_changes()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let mut suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let mut confirmation = suite.cases.first().ok_or("missing fixture case")?.clone();
    confirmation.id = "confirm-last-line".into();
    confirmation.argv = vec!["-n".into(), "1".into(), "@input:confirmation".into()];
    confirmation.correctness = vec![CorrectnessRule::TailSlice {
        dataset: "confirmation".into(),
        unit: TailUnit::Lines,
        count: 1,
        stream: Stream::Stdout,
    }];
    suite.cases.push(confirmation);
    let mut confirmation_input = suite
        .datasets
        .first()
        .ok_or("missing fixture dataset")?
        .clone();
    confirmation_input.id = "confirmation".into();
    confirmation_input.output = "confirmation.txt".into();
    suite.datasets.push(confirmation_input);
    let request = ExperimentRequest {
        id: "selection".into(),
        hypothesis: "improve".into(),
        change_summary: "change".into(),
        requested_previous: "base".into(),
        requested_candidate: "HEAD".into(),
    };
    let mut original_id = None;
    for (index, case) in ["last-line", "confirm-last-line", "last-line", "last-line"]
        .into_iter()
        .enumerate()
    {
        let run = store.begin_tagged_run(&suite, &request)?;
        let mut manifest = resolved_manifest(&store, &run, &source)?;
        let contract = manifest
            .contract
            .as_mut()
            .ok_or("missing fixture contract")?;
        contract.suite = suite.clone();
        if index == 2 {
            contract.analysis_policy = "changed-policy".into();
        }
        if index == 3 {
            let previous = manifest
                .roles
                .get(&Role::Previous)
                .ok_or("previous missing")?;
            let mut build = previous.build.clone().ok_or("build missing")?;
            build.source_sha = "2222222222222222222222222222222222222222".into();
            manifest.roles.insert(
                Role::Previous,
                register_binary(&source, Some(build), &store)?,
            );
        }
        if index == 1 {
            manifest.inputs.push(InputRecord {
                dataset: "confirmation".into(),
                path: source.clone(),
                file: fingerprint(&source)?,
            });
            let candidate = root.path().join("candidate2");
            std::fs::write(&candidate, b"new candidate")?;
            manifest
                .roles
                .insert(Role::Candidate, register_binary(&candidate, None, &store)?);
            require(
                original_id.as_ref() == Some(&contract.identity()?),
                "candidate or selection changed contract identity",
            )?;
        }
        if index == 0 {
            original_id = Some(contract.identity()?);
        }
        manifest.selected_cases = vec![case.into()];
        manifest.experiment = Some(request.clone());
        let result = run.finish(&manifest, &complete());
        if index < 2 {
            result?;
        } else {
            require(result.is_err(), "experiment policy/base drift accepted")?;
        }
    }
    require(
        std::fs::read_dir(store.root().join("experiments/selection/attempts"))?.count() == 4,
        "rejected attempts were lost",
    )?;
    Ok(())
}

#[test]
fn unresolved_failed_tools_do_not_freeze_experiment_contract()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let request = ExperimentRequest {
        id: "unresolved-tools".into(),
        hypothesis: "improve".into(),
        change_summary: "change".into(),
        requested_previous: "base".into(),
        requested_candidate: "HEAD".into(),
    };
    let anchor = store
        .root()
        .join("experiments/unresolved-tools/experiment.json");
    for outcome in [RunOutcome::Failed, RunOutcome::Incomplete] {
        let run = store.begin_tagged_run(&suite, &request)?;
        let mut manifest = resolved_manifest(&store, &run, &source)?;
        manifest.experiment = Some(request.clone());
        manifest.tool_paths = None;
        manifest
            .contract
            .as_mut()
            .ok_or("missing contract")?
            .harness
            .file
            .sha256 = "0".repeat(64);
        let result = RunResult {
            schema_version: 1,
            outcome,
            message: Some("tool resolution failed".into()),
        };
        let bundle = run.finish(&manifest, &result)?;
        let retained: RunManifest =
            serde_json::from_slice(&std::fs::read(bundle.path.join("manifest.json"))?)?;
        require(
            retained == manifest,
            "partial failed manifest was not retained",
        )?;
        require(
            bundle.result == result,
            "failed/incomplete outcome was not retained",
        )?;
        require(
            !anchor.exists(),
            "unverified failed tool identity froze experiment",
        )?;
    }
    let run = store.begin_tagged_run(&suite, &request)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    manifest.experiment = Some(request);
    let bundle = run.finish(&manifest, &complete())?;
    let anchor: ExperimentRecord = serde_json::from_slice(&std::fs::read(anchor)?)?;
    require(
        Some(anchor.contract) == bundle.manifest.contract,
        "valid attempt did not establish its verified contract",
    )?;
    require(
        std::fs::read_dir(store.root().join("experiments/unresolved-tools/attempts"))?.count() == 3,
        "failed or resolved attempt reference was lost",
    )?;
    Ok(())
}

#[test]
fn tagged_known_role_policy_drift_is_rejected_and_attempts_are_retained()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let request = ExperimentRequest {
        id: "known-policies".into(),
        hypothesis: "improve".into(),
        change_summary: "change".into(),
        requested_previous: "base".into(),
        requested_candidate: "HEAD".into(),
    };
    let run = store.begin_tagged_run(&suite, &request)?;
    let mut initial = resolved_manifest(&store, &run, &source)?;
    initial.experiment = Some(request.clone());
    let mut build = initial
        .roles
        .get(&Role::Previous)
        .and_then(|record| record.build.clone())
        .ok_or("missing previous build")?;
    build.source_sha = "2222222222222222222222222222222222222222".into();
    initial.roles.insert(
        Role::Candidate,
        register_binary(&source, Some(build.clone()), &store)?,
    );
    run.finish(&initial, &complete())?;
    let anchor_path = store
        .root()
        .join("experiments/known-policies/experiment.json");
    let anchor = std::fs::read(&anchor_path)?;
    for (role, changed_field, verified_tools) in [
        (Role::Candidate, "compiler", true),
        (Role::Candidate, "target", true),
        (Role::Candidate, "flags", true),
        (Role::Candidate, "features", true),
        (Role::Candidate, "environment", true),
        (Role::Reference, "compiler", true),
        (Role::Candidate, "compiler", false),
    ] {
        let run = store.begin_tagged_run(&suite, &request)?;
        let path = run.path().to_path_buf();
        let mut manifest = resolved_manifest(&store, &run, &source)?;
        manifest.experiment = Some(request.clone());
        let mut changed = build.clone();
        match changed_field {
            "compiler" => changed.policy.compiler = "different compiler".into(),
            "target" => changed.policy.target = "different target".into(),
            "features" => changed.policy.settings.features.push("fast".into()),
            "environment" => changed.policy.environment_hash = Some("a".repeat(64)),
            "flags" => changed
                .policy
                .settings
                .rustflags
                .push("-Copt-level=1".into()),
            _ => return Err("invalid test field".into()),
        }
        manifest
            .roles
            .insert(role, register_binary(&source, Some(changed), &store)?);
        let mut result = complete();
        if !verified_tools {
            manifest.tool_paths = None;
            result.outcome = RunOutcome::Failed;
            result.message = Some("tool resolution failed".into());
        }
        require(
            run.finish(&manifest, &result).is_err(),
            "tagged known role policy drift was accepted",
        )?;
        let retained: RunManifest =
            serde_json::from_slice(&std::fs::read(path.join("manifest.json"))?)?;
        require(retained == manifest, "rejected attempt manifest was lost")?;
        let status: RunResult = serde_json::from_slice(&std::fs::read(path.join("status.json"))?)?;
        require(
            status.outcome == RunOutcome::Incomplete,
            "rejected attempt was marked complete",
        )?;
        require(
            std::fs::read(&anchor_path)? == anchor,
            "rejected attempt changed frozen policy",
        )?;
    }
    // A labelled unknown prebuilt remains usable; no compiler policy is inferred for it.
    let run = store.begin_tagged_run(&suite, &request)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    manifest.experiment = Some(request);
    require(
        manifest
            .roles
            .get(&Role::Candidate)
            .is_some_and(|record| record.build.is_none()),
        "fixture lost unknown provenance",
    )?;
    run.finish(&manifest, &complete())?;
    require(
        std::fs::read_dir(store.root().join("experiments/known-policies/attempts"))?.count() == 9,
        "policy rejection lost an attempt reference",
    )?;
    Ok(())
}

#[test]
fn untagged_known_policy_differences_remain_product_comparisons()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.path().join("evidence"))?;
    let source = root.path().join("tool");
    std::fs::write(&source, b"abc")?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let run = store.begin_run(&suite)?;
    let mut manifest = resolved_manifest(&store, &run, &source)?;
    let mut changed = manifest
        .roles
        .get(&Role::Previous)
        .and_then(|record| record.build.clone())
        .ok_or("missing previous build")?;
    changed.policy.compiler = "different compiler".into();
    changed.policy.target = "different target".into();
    changed
        .policy
        .settings
        .rustflags
        .push("-Copt-level=1".into());
    for role in [Role::Candidate, Role::Reference] {
        manifest.roles.insert(
            role,
            register_binary(&source, Some(changed.clone()), &store)?,
        );
    }
    let bundle = run.finish(&manifest, &complete())?;
    require(
        bundle.manifest == manifest,
        "untagged build differences were not retained",
    )?;
    store.load_run(&bundle.manifest.run_id)?;
    Ok(())
}

#[path = "common/build.rs"]
mod build_support;

#[test]
fn builds_resolved_revisions_with_isolated_outputs_and_ignores_dirty_edits()
-> Result<(), Box<dyn std::error::Error>> {
    use build_support::{Fixture, command, runner};
    use cli_bench::*;
    let mut fixture = Fixture::new()?;
    fixture.tools.environment.insert(
        "BUILD_PRIVATE_TOKEN".into(),
        "secret-value-never-publish".into(),
    );
    let runner = runner();
    let first_revision = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
    std::fs::write(
        fixture.repo.join("src/main.rs"),
        "fn main() { println!(\"second\"); }\n",
    )?;
    let manifest =
        std::fs::read_to_string(fixture.repo.join("Cargo.toml"))?.replace("0.1.0", "0.2.0");
    let lock = std::fs::read_to_string(fixture.repo.join("Cargo.lock"))?.replace("0.1.0", "0.2.0");
    std::fs::write(fixture.repo.join("Cargo.toml"), manifest)?;
    std::fs::write(fixture.repo.join("Cargo.lock"), lock)?;
    command(
        &fixture.repo,
        &fixture.git.tool.path,
        &["commit", "-am", "second", "--quiet"],
    )?;
    let second_revision = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
    std::fs::write(
        fixture.repo.join("src/main.rs"),
        "this dirty source does not compile",
    )?;
    let dirty = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
    require(
        dirty.dirty && dirty.sha == second_revision.sha,
        "dirty HEAD was not resolved to committed tree",
    )?;
    let store = Store::open(&fixture.root.path().join("evidence"))?;
    let first = build_revision(&fixture.request(first_revision.clone()), &store, &runner)?;
    require(
        !serde_json::to_string(&first)?.contains("secret-value-never-publish"),
        "build environment secret leaked into artifact",
    )?;
    let second = build_revision(&fixture.request(second_revision), &store, &runner)?;
    require(
        first.build.as_ref().map(|build| &build.policy)
            == second.build.as_ref().map(|build| &build.policy),
        "ephemeral snapshot paths changed shared build policy",
    )?;
    require(
        first.file != second.file,
        "new build overwrote or reused old output",
    )?;
    require(
        command(&fixture.repo, &store.artifact_path(&first), &[])? == "first",
        "moving HEAD replaced resolved revision",
    )?;
    require(
        command(&fixture.repo, &store.artifact_path(&second), &[])? == "second",
        "dirty edits entered committed build",
    )?;
    require(
        std::fs::read_to_string(fixture.repo.join("src/main.rs"))?
            == "this dirty source does not compile",
        "active checkout was modified",
    )?;
    require(
        first.build.as_ref().map(|build| &build.lockfile)
            != second.build.as_ref().map(|build| &build.lockfile),
        "different revision lockfiles were lost",
    )?;
    let mut featured_request = fixture.request(first_revision.clone());
    featured_request.policy.features = vec!["fast".into()];
    let featured = build_revision(&featured_request, &store, &runner)?;
    require(
        featured.id != first.id
            && featured
                .build
                .as_ref()
                .is_some_and(|build| build.resolved_features == ["fast"]),
        "changed features reused old build provenance",
    )?;
    let cached = build_revision(&fixture.request(first_revision.clone()), &store, &runner)?;
    require(cached == first, "verified cache changed provenance")?;
    std::fs::write(store.artifact_path(&first), b"overwritten baseline")?;
    require(
        build_revision(&fixture.request(first_revision), &store, &runner).is_err(),
        "corrupt baseline cache reused",
    )?;
    Ok(())
}

#[test]
fn binds_prebuilt_roles_without_inventing_compiler_provenance_or_missing_reference()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    use std::os::unix::fs::PermissionsExt;
    let root = assert_fs::TempDir::new()?;
    let executable = root.path().join("tool");
    std::fs::write(&executable, "#!/bin/sh\nexit 0\n")?;
    std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o755))?;
    let home = root.path().join("home");
    let config = root.path().join("config");
    std::fs::create_dir(&home)?;
    std::fs::create_dir(&config)?;
    let store = Store::open(&root.path().join("evidence"))?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let mut request = RunRequest {
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
    let bindings = bind_roles(&request, &suite, &store, &build_support::runner())?;
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
        bind_roles(&request, &suite, &store, &build_support::runner()).is_err(),
        "missing explicit reference was silently ignored",
    )?;
    request.reference = None;
    require(
        bind_roles(&request, &suite, &store, &build_support::runner()).is_err(),
        "comparison without baseline was accepted",
    )?;
    Ok(())
}

#[test]
fn rejects_unsupported_committed_sources_and_literal_malicious_refs()
-> Result<(), Box<dyn std::error::Error>> {
    use build_support::{Fixture, command, runner};
    use cli_bench::*;
    let fixture = Fixture::new()?;
    let runner = runner();
    for revision in [
        "--help",
        "-cfoo=bar",
        "HEAD;touch marker",
        "$(touch marker)",
    ] {
        require(
            resolve_revision(&fixture.repo, revision, &fixture.git, &runner).is_err(),
            "unsafe ref was accepted",
        )?;
    }
    require(
        !fixture.repo.join("marker").exists(),
        "ref text executed as shell source",
    )?;
    let original = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
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
    let submodule = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
    let error = build_revision(&fixture.request(submodule), &store, &runner)
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
    let lfs = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
    let error = build_revision(&fixture.request(lfs), &store, &runner)
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
    use build_support::{Fixture, runner};
    use cli_bench::*;
    use std::os::unix::fs::PermissionsExt;
    let fixture = Fixture::new()?;
    let runner = runner();
    let revision = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
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
            "missing" => "printf '%s\\n' '{\"reason\":\"build-finished\",\"success\":true}'".into(),
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
        let error = build_revision(&request, &store, &runner)
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
    use build_support::{Fixture, command, runner};
    use cli_bench::*;
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
    let revision = resolve_revision(&fixture.repo, "HEAD", &fixture.git, &runner)?;
    let store = Store::open(&fixture.root.path().join("evidence"))?;
    let artifact = build_revision(&fixture.request(revision), &store, &runner)?;
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

// Tiny original literal fault fixtures exercise plumbing only, never benchmark generation.
fn dataset_bindings(
    root: &std::path::Path,
    store: &Store,
    script: &str,
) -> Result<cli_bench::RoleBindings, Box<dyn std::error::Error>> {
    use std::os::unix::fs::PermissionsExt;
    let program = root.join("literal-fixture-generator");
    std::fs::write(&program, format!("#!/bin/sh\n{script}\n"))?;
    std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o700))?;
    let artifact = register_binary(&program, None, store)?;
    std::fs::create_dir_all(root.join("home"))?;
    std::fs::create_dir_all(root.join("config"))?;
    Ok(cli_bench::RoleBindings {
        roles: std::collections::BTreeMap::new(),
        generator: Some(cli_bench::BoundExecutable {
            path: store.artifact_path(&artifact),
            artifact,
        }),
        environment: std::collections::BTreeMap::new(),
        home: root.join("home"),
        config: root.join("config"),
        pipeline: None,
    })
}
fn dataset_runner() -> cli_bench::ProcessRunner {
    cli_bench::ProcessRunner::new(cli_bench::ExecutionPolicy {
        timeout: std::time::Duration::from_millis(250),
        max_stream_bytes: 1024,
        cancellation: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
    })
}
const LITERAL_GENERATOR: &str = r#"
[ "$1" = text ] || exit 8
[ "$8" = --seed ] || exit 9
[ "$LC_ALL" = C ] || exit 10
for output do :; done
printf 'fixture diagnostic\n' >&2
if [ -f "$HOME/drift" ]; then printf 'WXYZ\nIJKL\n' > "$output"; else printf 'ABCD\nEFGH\n' > "$output"; fi
"#;
#[test]
fn dataset_fixture_materializes_reuses_and_replays_across_stores()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let bindings = dataset_bindings(root.path(), &store, LITERAL_GENERATOR)?;
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    let datasets = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    verify_datasets(&datasets)?;
    let input = datasets.inputs.get("tiny").ok_or("missing input")?;
    require(
        std::fs::read(&input.path)? == b"ABCD\nEFGH\n",
        "wrong fixture bytes",
    )?;
    let record = datasets
        .generations
        .get("tiny")
        .ok_or("missing provenance")?;
    require(record.shape_bytes == [10], "shape evidence absent")?;
    require(
        record.identity.generator.build.is_none(),
        "invented fixture build",
    )?;
    require(record.stderr.bytes == 19, "diagnostics not retained")?;
    std::fs::write(bindings.home.join("drift"), b"literal fault switch")?;
    let cached = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    require(cached.inputs == datasets.inputs, "cache was regenerated")?;
    let other = Store::open(&root.join("replay"))?;
    let mut replay_bindings = bindings.clone();
    let generator = bindings.generator.as_ref().ok_or("missing generator")?;
    let retained = register_binary(&generator.path, None, &other)?;
    replay_bindings.generator = Some(BoundExecutable {
        path: other.artifact_path(&retained),
        artifact: retained,
    });
    let expected = datasets.clone();
    let replay = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &replay_bindings,
        expected: Some(&expected),
    };
    require(
        prepare_datasets(&suite, &replay, &other, &dataset_runner()).is_err(),
        "seeded drift accepted",
    )?;
    require(
        expected.inputs == datasets.inputs && expected.generations == datasets.generations,
        "replay overwrote expectations",
    )?;
    let failures: Vec<_> =
        std::fs::read_dir(other.root().join("datasets"))?.collect::<Result<_, _>>()?;
    require(
        failures
            .iter()
            .any(|entry| entry.path().join("failure.json").is_file()),
        "failed replay diagnostics absent",
    )?;
    std::fs::remove_file(bindings.home.join("drift"))?;
    std::fs::remove_file(&input.path)?;
    let regenerated = prepare_datasets(&suite, &replay, &other, &dataset_runner())?;
    require(
        regenerated.inputs.get("tiny").map(|value| &value.file) == Some(&input.file),
        "cross-store replay changed identity",
    )?;
    Ok(())
}

#[test]
fn dataset_preflight_rejects_all_over_budget_inputs_before_spawning()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let bindings = dataset_bindings(
        root.path(),
        &store,
        "/usr/bin/touch \"$HOME/spawned\"; exit 23",
    )?;
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    let mut suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let mut second = suite.datasets.first().ok_or("missing dataset")?.clone();
    second.id = "other".into();
    second.output = "other.txt".into();
    suite.datasets.push(second);
    suite.limits.max_generated_bytes = 19;
    let error = prepare_datasets(&suite, &request, &store, &dataset_runner())
        .err()
        .ok_or("aggregate overflow accepted")?;
    require(
        error.to_string().contains("budget"),
        "wrong aggregate failure",
    )?;
    require(
        !bindings.home.join("spawned").exists(),
        "generator ran before aggregate preflight",
    )?;
    suite.limits.max_generated_bytes = 20;
    suite.limits.max_generated_file_bytes = 9;
    let error = prepare_datasets(&suite, &request, &store, &dataset_runner())
        .err()
        .ok_or("file overflow accepted")?;
    require(
        error.to_string().contains("budget"),
        "wrong file budget failure",
    )?;
    require(
        !bindings.home.join("spawned").exists(),
        "generator ran before file preflight",
    )?;
    Ok(())
}

#[test]
fn dataset_faults_keep_diagnostics_and_never_publish_verified_entries()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    for (script, reason) in [
        ("printf 'failed fixture' >&2; exit 7", "expected exit"),
        ("printf 'timeout fixture' >&2; /bin/sleep 3", "Timeout"),
        ("printf 'missing fixture' >&2; exit 0", "No such file"),
        (
            "for output do :; done; printf '0123456789012345' > \"$output\"; /bin/sleep 3",
            "FileLimit",
        ),
        (
            "for output do :; done; printf 'bad!' > \"$output\"",
            "shape",
        ),
        (
            "for output do :; done; /bin/ln -s /dev/null \"$output\"",
            "regular",
        ),
    ] {
        let root = assert_fs::TempDir::new()?;
        let store = Store::open(&root.join("store"))?;
        let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
        let bindings = dataset_bindings(root.path(), &store, script)?;
        let request = DatasetPreparation {
            profile: MeasurementProfile::Full,
            bindings: &bindings,
            expected: None,
        };
        let error = prepare_datasets(&suite, &request, &store, &dataset_runner())
            .err()
            .ok_or("generator fault accepted")?;
        require(
            error.to_string().contains(reason),
            &format!("wrong fault: {error}"),
        )?;
        let entries: Vec<_> =
            std::fs::read_dir(store.root().join("datasets"))?.collect::<Result<_, _>>()?;
        require(
            entries.len() == 1
                && entries
                    .iter()
                    .all(|e| e.path().join("failure.json").is_file()),
            "failure evidence not retained",
        )?;
        if reason == "FileLimit" {
            let failure = entries
                .first()
                .ok_or("missing quota failure")?
                .path()
                .join("failure.json");
            let evidence: serde_json::Value = serde_json::from_slice(&std::fs::read(failure)?)?;
            require(
                evidence
                    .get("observed")
                    .is_some_and(serde_json::Value::is_null),
                "oversized output was fully hashed after quota failure",
            )?;
            require(
                evidence
                    .get("observed_bytes")
                    .and_then(serde_json::Value::as_u64)
                    == Some(16),
                "quota observation size absent",
            )?;
        }
    }
    Ok(())
}

#[test]
fn dataset_cached_hash_mismatch_records_expected_and_observed_without_overwrite()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let bindings = dataset_bindings(root.path(), &store, LITERAL_GENERATOR)?;
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    let original = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    let input = original.inputs.get("tiny").ok_or("missing input")?;
    let generation = original
        .generations
        .get("tiny")
        .ok_or("missing generation")?;
    let metadata = store
        .root()
        .join("datasets")
        .join(&generation.recipe_hash)
        .join("generation.json");
    let original_metadata = std::fs::read(&metadata)?;
    std::fs::write(&input.path, b"WXYZ\nIJKL\n")?;
    require(
        prepare_datasets(&suite, &request, &store, &dataset_runner()).is_err(),
        "cache corruption accepted",
    )?;
    require(
        std::fs::read(metadata)? == original_metadata,
        "cache metadata overwritten",
    )?;
    let failure = std::fs::read_dir(store.root().join("datasets"))?
        .filter_map(Result::ok)
        .find(|e| e.path().join("failure.json").is_file())
        .ok_or("missing failure evidence")?;
    let evidence: serde_json::Value =
        serde_json::from_slice(&std::fs::read(failure.path().join("failure.json"))?)?;
    require(
        evidence.get("expected") == Some(&serde_json::to_value(&input.file)?),
        "expected hash absent from failure",
    )?;
    require(
        evidence.get("observed") == Some(&serde_json::to_value(fingerprint(&input.path)?)?),
        "observed hash absent from failure",
    )?;
    Ok(())
}

#[test]
fn dataset_cache_size_drift_is_rejected_before_hash_verification()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let bindings = dataset_bindings(root.path(), &store, LITERAL_GENERATOR)?;
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    let datasets = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    let input = datasets.inputs.get("tiny").ok_or("missing fixture")?;
    std::fs::write(&input.path, b"oversized literal fixture")?;
    let error = prepare_datasets(&suite, &request, &store, &dataset_runner())
        .err()
        .ok_or("cache size drift accepted")?;
    require(
        error.to_string().contains("dataset size mismatch"),
        "size drift reached hash verification",
    )?;
    let error = verify_datasets(&datasets)
        .err()
        .ok_or("set size drift accepted")?;
    require(
        error.to_string().contains("dataset size mismatch"),
        "set size drift reached hash verification",
    )?;
    Ok(())
}

#[test]
fn dataset_selected_profile_and_bound_generator_are_independent_of_candidate()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let mut suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let mut bindings = dataset_bindings(root.path(), &store, LITERAL_GENERATOR)?;
    let other = root.join("measured-candidate");
    std::fs::write(&other, b"deliberately not the generator")?;
    let artifact = register_binary(&other, None, &store)?;
    bindings.roles.insert(
        Role::Candidate,
        BoundExecutable {
            path: store.artifact_path(&artifact),
            artifact,
        },
    );
    let dataset = suite.datasets.first_mut().ok_or("missing recipe")?;
    dataset.profiles.insert(
        MeasurementProfile::Smoke,
        DatasetRecipe {
            argv: dataset.argv.clone(),
            checks: dataset.checks.clone(),
        },
    );
    dataset.checks = vec![CorrectnessRule::TextShape {
        records: 1_000_000,
        words_per_record: 1,
        word_length: 4,
    }];
    suite.limits.max_generated_bytes = 10;
    suite.limits.max_generated_file_bytes = 10;
    let request = DatasetPreparation {
        profile: MeasurementProfile::Smoke,
        bindings: &bindings,
        expected: None,
    };
    let datasets = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    require(
        datasets.inputs.get("tiny").map(|i| i.file.bytes) == Some(10),
        "wrong selected profile",
    )?;
    verify_datasets(&datasets)?;
    Ok(())
}

#[test]
fn dataset_recipe_and_bound_identity_failures_precede_generator_execution()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let bindings = dataset_bindings(root.path(), &store, LITERAL_GENERATOR)?;
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    let datasets = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    let replay = DatasetPreparation {
        expected: Some(&datasets),
        ..request
    };
    for edit in ["seed", "random", "escape"] {
        let mut changed = suite.clone();
        let dataset = changed.datasets.first_mut().ok_or("missing recipe")?;
        match edit {
            "seed" => {
                *dataset.argv.get_mut(8).ok_or("missing seed")? = "43".into();
            }
            "random" => {
                dataset.argv.drain(7..9);
            }
            _ => {
                dataset.output = "../escape".into();
            }
        }
        require(
            prepare_datasets(&changed, &replay, &store, &dataset_runner()).is_err(),
            "invalid replay recipe accepted",
        )?;
    }
    let mut malformed = datasets.clone();
    malformed
        .generations
        .get_mut("tiny")
        .ok_or("missing record")?
        .argv = vec!["forged command".into()];
    let bad_expected = DatasetPreparation {
        expected: Some(&malformed),
        ..replay
    };
    require(
        prepare_datasets(&suite, &bad_expected, &store, &dataset_runner()).is_err(),
        "malformed replay argv accepted",
    )?;
    let generator = bindings.generator.as_ref().ok_or("missing generator")?;
    std::fs::write(&generator.path, b"changed generator")?;
    require(
        prepare_datasets(&suite, &replay, &store, &dataset_runner()).is_err(),
        "changed generator accepted",
    )?;
    Ok(())
}

#[test]
fn dataset_path_records_are_checked_before_cache_publication()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let bindings = dataset_bindings(
        root.path(),
        &store,
        "for output do :; done; printf '../x\\n' > \"$output\"",
    )?;
    let mut suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let dataset = suite.datasets.first_mut().ok_or("missing recipe")?;
    dataset.argv = vec![
        "records".into(),
        "-r".into(),
        "../x".into(),
        "@output".into(),
    ];
    dataset.checks = vec![CorrectnessRule::Records {
        records: vec!["../x".into()],
        repeat: 1,
        cycles: 1,
    }];
    suite.cases.first_mut().ok_or("missing case")?.argv = vec!["@records:tiny".into()];
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    require(
        prepare_datasets(&suite, &request, &store, &dataset_runner()).is_err(),
        "escaping generated record accepted",
    )?;
    let entries: Vec<_> =
        std::fs::read_dir(store.root().join("datasets"))?.collect::<Result<_, _>>()?;
    require(
        entries
            .iter()
            .all(|e| e.path().join("failure.json").exists()),
        "unsafe paths published",
    )?;
    Ok(())
}

#[test]
#[ignore = "opt-in real Biggie CLI: set CLI_BENCH_BIGGIE to an absolute built binary"]
fn native_biggie_materializes_tiny_recipes_and_reuses_verified_inputs()
-> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let binary = std::path::PathBuf::from(
        std::env::var_os("CLI_BENCH_BIGGIE").ok_or("set CLI_BENCH_BIGGIE explicitly")?,
    );
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let suite = parse_suite(include_str!("inputs/tiny-datasets.toml"))?;
    std::fs::create_dir(root.join("home"))?;
    std::fs::create_dir(root.join("config"))?;
    let artifact = register_binary(&binary, None, &store)?;
    let bindings = RoleBindings {
        roles: std::collections::BTreeMap::new(),
        generator: Some(BoundExecutable {
            path: store.artifact_path(&artifact),
            artifact,
        }),
        environment: suite.environment.clone(),
        home: root.join("home"),
        config: root.join("config"),
        pipeline: None,
    };
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    let first = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    verify_datasets(&first)?;
    require(
        first
            .inputs
            .values()
            .map(|input| input.file.bytes)
            .sum::<u64>()
            == 29,
        "wrong native recipe sizes",
    )?;
    let second = prepare_datasets(&suite, &request, &store, &dataset_runner())?;
    require(
        first.inputs == second.inputs,
        "native cache did not preserve inputs",
    )?;
    Ok(())
}

#[test]
fn dataset_rejects_output_parent_symlink_replacement() -> Result<(), Box<dyn std::error::Error>> {
    use cli_bench::*;
    let root = assert_fs::TempDir::new()?;
    let store = Store::open(&root.join("store"))?;
    let bindings = dataset_bindings(
        root.path(),
        &store,
        r#"for output do :; done
parent=${output%/*}
/bin/rmdir "$parent"
/bin/ln -s "$HOME" "$parent"
printf 'ABCD\nEFGH\n' > "$output""#,
    )?;
    let mut suite = parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    suite.datasets.first_mut().ok_or("missing recipe")?.output = "nested/tiny.txt".into();
    let request = DatasetPreparation {
        profile: MeasurementProfile::Full,
        bindings: &bindings,
        expected: None,
    };
    require(
        prepare_datasets(&suite, &request, &store, &dataset_runner()).is_err(),
        "symlink parent output published",
    )?;
    for entry in std::fs::read_dir(store.root().join("datasets"))? {
        let entry = entry?;
        require(
            entry
                .file_name()
                .to_str()
                .is_some_and(|name| name.starts_with("pending-") || name.starts_with("failed-")),
            "unsafe output reached immutable cache path",
        )?;
    }
    Ok(())
}
