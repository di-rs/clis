use assert_cmd::cargo::cargo_bin_cmd;
use cli_bench as bench_api;
use predicates::prelude::*;

#[test]
fn help_identifies_the_interface_on_stdout() {
    for flag in ["-h", "--help"] {
        cargo_bin_cmd!()
            .env_remove("CLIS_LOG_LEVEL")
            .arg(flag)
            .assert()
            .success()
            .stderr("")
            .stdout(predicate::str::contains(
                "Correctness-checked CLI benchmark evidence",
            ))
            .stdout(predicate::str::contains("Usage: cli-bench"))
            .stdout(predicate::str::contains("--log-level"));
    }
}

#[test]
fn version_identifies_the_package_on_stdout() {
    for flag in ["-V", "--version"] {
        let assertion = cargo_bin_cmd!()
            .env_remove("CLIS_LOG_LEVEL")
            .arg(flag)
            .assert()
            .success()
            .stderr("");
        assert_eq!(assertion.get_output().stdout, b"cli-bench 0.1.0\n");
    }
}

#[test]
fn no_arguments_shows_the_available_interface() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .assert()
        .success()
        .stderr("")
        .stdout(predicate::str::contains("Usage: cli-bench"));
}

#[test]
fn invalid_logging_returns_cli_input_status() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .args(["-L", "invalid"])
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicate::str::contains("invalid --log-level"));
}

#[test]
fn unknown_arguments_return_cli_input_status() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .arg("--unknown")
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicate::str::contains("unexpected argument"));
}

#[test]
fn explicit_logging_overrides_an_invalid_environment() {
    cargo_bin_cmd!()
        .env("CLIS_LOG_LEVEL", "invalid")
        .args(["-L", "off"])
        .assert()
        .success()
        .stderr("")
        .stdout(predicate::str::contains("Usage: cli-bench"));
}

#[path = "common/build.rs"]
mod build_support;

#[test]
fn build_retains_artifact_and_announces_dirty_head_exclusion()
-> Result<(), Box<dyn std::error::Error>> {
    let fixture = build_support::Fixture::new()?;
    std::fs::write(fixture.repo.join("src/main.rs"), "uncommitted source")?;
    let evidence = fixture.root.path().join("evidence");
    let assertion = cargo_bin_cmd!()
        .current_dir(&fixture.repo)
        .env_remove("CLIS_LOG_LEVEL")
        .args(["build", "-p", "tiny", "-r", "HEAD", "-d"])
        .arg(&evidence)
        .assert()
        .success()
        .stderr(predicate::str::contains("uncommitted edits are excluded"));
    let record: cli_bench::ArtifactRecord = serde_json::from_slice(&assertion.get_output().stdout)?;
    cli_bench::Store::open(&evidence)?.verify_artifact(&record)?;
    Ok(())
}

#[test]
fn build_rejects_ambiguous_packages_without_guessing_a_binary()
-> Result<(), Box<dyn std::error::Error>> {
    let fixture = build_support::Fixture::new()?;
    std::fs::create_dir(fixture.repo.join("src/bin"))?;
    std::fs::write(fixture.repo.join("src/bin/other.rs"), "fn main() {}")?;
    build_support::command(&fixture.repo, &fixture.git.tool.path, &["add", "."])?;
    build_support::command(
        &fixture.repo,
        &fixture.git.tool.path,
        &["commit", "--quiet", "-m", "two binaries"],
    )?;
    cargo_bin_cmd!()
        .current_dir(&fixture.repo)
        .env_remove("CLIS_LOG_LEVEL")
        .args(["build", "-p", "tiny", "-r", "HEAD", "-d"])
        .arg(fixture.root.path().join("evidence"))
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("multiple binaries"));
    Ok(())
}

#[test]
fn missing_suite_returns_operational_failure() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .args(["run", "-s", "tailr", "-a", "/candidate", "-x", "/reference"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("suite"));
}

#[test]
fn build_fixtures_ignore_git_hook_repository_redirection() -> Result<(), Box<dyn std::error::Error>>
{
    let root = assert_fs::TempDir::new()?;
    build_support::command(
        root.path(),
        std::path::Path::new("/usr/bin/git"),
        &["init", "--quiet"],
    )?;
    let before = std::fs::read(root.path().join(".git/config"))?;
    let environment = ["PATH", "HOME", "CARGO_HOME", "RUSTUP_HOME", "TMPDIR"]
        .into_iter()
        .filter_map(|key| std::env::var_os(key).map(|value| (key, value)));
    assert_cmd::Command::new(std::env::current_exe()?)
        .env_clear()
        .envs(environment)
        .env("GIT_DIR", root.path().join(".git"))
        .env("GIT_WORK_TREE", root.path())
        .args([
            "--exact",
            "build_rejects_ambiguous_packages_without_guessing_a_binary",
        ])
        .timeout(std::time::Duration::from_secs(60))
        .assert()
        .success();
    if std::fs::read(root.path().join(".git/config"))? != before {
        return Err("fixture modified redirected repository configuration".into());
    }
    if !build_support::command(
        root.path(),
        std::path::Path::new("/usr/bin/git"),
        &["status", "--porcelain"],
    )?
    .is_empty()
    {
        return Err("fixture staged files in redirected repository".into());
    }
    Ok(())
}

#[test]
fn check_prebuilt_cli_produces_plain_json_without_timing_tools()
-> Result<(), Box<dyn std::error::Error>> {
    composed_cli(true)
}
#[test]
#[ignore = "requires native time resource permissions"]
fn native_slow_candidate_cli_exits_zero() -> Result<(), Box<dyn std::error::Error>> {
    composed_cli(false)
}
fn composed_cli(check_only: bool) -> Result<(), Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    let lock =
        cli_bench::MeasurementLock::acquire(&std::sync::atomic::AtomicBool::new(false), || Ok(()))?;
    let fixture = validation_support::Fixture::from_root_and_lock(
        root,
        lock,
        "# slower candidate\nprintf 'EFGH\\n'",
        "printf 'EFGH\\n'",
    )?;
    let suite = fixture.root.join("suite.toml");
    let submitted = format!(
        "# exact submitted suite\n{}",
        toml::to_string(&fixture.suite)?
    );
    std::fs::write(&suite, &submitted)?;
    let engine = slow_engine(&fixture)?;
    drop(fixture.measurement_lock);
    let cli_bench::ExecutableSource::Prebuilt(candidate) = &fixture.request.candidate else {
        return Err("candidate".into());
    };
    let Some(cli_bench::ExecutableSource::Prebuilt(previous)) = &fixture.request.previous else {
        return Err("previous".into());
    };
    let Some(cli_bench::ExecutableSource::Prebuilt(generator)) = &fixture.request.generator else {
        return Err("generator".into());
    };
    let assertion = cargo_bin_cmd!()
        .current_dir(fixture.root.path())
        .args(["check", "-F", "selected-feature", "-f", "json", "-s"])
        .arg(&suite)
        .arg("-a")
        .arg(candidate)
        .arg("-p")
        .arg(previous)
        .arg("-g")
        .arg(generator)
        .arg("-d")
        .arg(fixture.store.root())
        .env("NO_COLOR", "1")
        .assert()
        .success();
    let record: cli_bench::PublicationRecord =
        serde_json::from_slice(&assertion.get_output().stdout)?;
    if record.result.outcome != cli_bench::RunOutcome::Complete {
        return Err("check failed".into());
    }
    if !record.analysis.timing_samples.is_empty() {
        return Err("check measured timing".into());
    }
    verify_offline_comparison(&fixture.store, &record, &submitted)?;
    if check_only {
        verify_portable_workflow(fixture.root.path(), &fixture.store, &record)?;
    }
    if check_only {
        return Ok(());
    }
    let assertion = cargo_bin_cmd!()
        .current_dir(fixture.root.path())
        .args(["run", "-f", "json", "-s"])
        .arg(&suite)
        .arg("-a")
        .arg(candidate)
        .arg("-p")
        .arg(previous)
        .arg("-g")
        .arg(generator)
        .arg("-H")
        .arg(&engine.path)
        .arg("-d")
        .arg(fixture.store.root())
        .env("NO_COLOR", "1")
        .assert();
    let record: cli_bench::PublicationRecord =
        serde_json::from_slice(&assertion.get_output().stdout)?;
    if !assertion.get_output().status.success() {
        let diagnostics = rss_diagnostics(&fixture.store, &record);
        return Err(format!(
            "CLI failed: {:?}; diagnostics: {diagnostics:?}",
            record.result
        )
        .into());
    }
    if record
        .analysis
        .cases
        .first()
        .and_then(|case| case.comparisons.first())
        .map(|comparison| comparison.direction)
        != Some(cli_bench::Direction::Slower)
    {
        return Err("slow candidate did not produce advisory success".into());
    }
    Ok(())
}

fn rss_diagnostics(store: &cli_bench::Store, record: &cli_bench::PublicationRecord) -> Vec<String> {
    record
        .evidence
        .iter()
        .filter(|path| path.starts_with("raw/rss/") && path.ends_with(".stderr"))
        .map(|path| {
            std::fs::read_to_string(
                store
                    .root()
                    .join("runs")
                    .join(&record.manifest.run_id)
                    .join(path),
            )
            .unwrap_or_default()
        })
        .collect()
}

fn slow_engine(
    fixture: &validation_support::Fixture,
) -> Result<cli_bench::BoundTool, Box<dyn std::error::Error>> {
    let cli_bench::ExecutableSource::Prebuilt(candidate_path) = &fixture.request.candidate else {
        return Err("candidate".into());
    };
    let candidate_record = cli_bench::register_binary(candidate_path, None, &fixture.store)?;
    let engine = timing_support::engine(
        fixture,
        &format!(
            "case \"$command\" in *{}*) seconds=0.002;; *) seconds=0.001;; esac",
            candidate_record.id
        ),
    )?;
    let engine_source = std::fs::read_to_string(&engine.path)?
        .replace("times\":[0.001]", "times\":[%s]")
        .replace(
            "\"$command\" \"$status\" >",
            "\"$command\" \"$seconds\" \"$status\" >",
        );
    std::fs::write(&engine.path, engine_source)?;
    Ok(engine)
}

#[allow(
    dead_code,
    reason = "shared fixture has component-only operations unused by this CLI consumer"
)]
#[path = "common/validation.rs"]
mod validation_support;

fn verify_portable_workflow(
    root: &std::path::Path,
    store: &cli_bench::Store,
    record: &cli_bench::PublicationRecord,
) -> Result<(), Box<dyn std::error::Error>> {
    let held =
        cli_bench::MeasurementLock::acquire(&std::sync::atomic::AtomicBool::new(false), || Ok(()))?;
    let original = store.root().join("runs").join(&record.manifest.run_id);
    let portable = root.join("portable");
    cargo_bin_cmd!()
        .args(["export", "-i"])
        .arg(&original)
        .arg("-o")
        .arg(&portable)
        .args(["-I", "-B"])
        .env("PATH", "")
        .assert()
        .success();
    cargo_bin_cmd!()
        .args(["report", "-i"])
        .arg(&portable)
        .args(["-f", "json"])
        .env("PATH", "")
        .assert()
        .success();
    cargo_bin_cmd!()
        .args(["compare", "-i"])
        .arg(&portable)
        .args(["-b", "previous", "-a", "candidate", "-f", "json"])
        .env("PATH", "")
        .assert()
        .success();
    drop(held);
    let replay_store = root.join("replay-store");
    let replay = cargo_bin_cmd!()
        .current_dir(root)
        .args(["replay", "-i"])
        .arg(&portable)
        .arg("-d")
        .arg(&replay_store)
        .args(["-f", "json"])
        .env("PATH", "")
        .assert()
        .success();
    let replay: cli_bench::PublicationRecord = serde_json::from_slice(&replay.get_output().stdout)?;
    if replay.manifest.run_id == record.manifest.run_id
        || replay.manifest.contract != record.manifest.contract
    {
        return Err("strict CLI replay changed contract or ID".into());
    }
    cargo_bin_cmd!()
        .args(["history", "-d"])
        .arg(replay_store.join("runs"))
        .args(["-f", "json"])
        .env("PATH", "")
        .assert()
        .success();
    Ok(())
}

fn verify_offline_comparison(
    store: &cli_bench::Store,
    record: &cli_bench::PublicationRecord,
    submitted: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let path = store.root().join("runs").join(&record.manifest.run_id);
    if std::fs::read_to_string(path.join("suite.toml"))? != submitted {
        return Err("submitted source changed".into());
    }
    let resolved =
        cli_bench::parse_suite(&std::fs::read_to_string(path.join("resolved-suite.toml"))?)?;
    if resolved.build.features != ["selected-feature"] {
        return Err("resolved override missing".into());
    }
    let _held =
        cli_bench::MeasurementLock::acquire(&std::sync::atomic::AtomicBool::new(false), || Ok(()))?;
    let assertion = cargo_bin_cmd!()
        .args(["compare", "-i"])
        .arg(&path)
        .args(["-b", "candidate", "-a", "previous", "-f", "json"])
        .env("PATH", "")
        .env_remove("CLIS_LOG_LEVEL")
        .timeout(std::time::Duration::from_secs(3))
        .assert()
        .success()
        .stderr("");
    let projected: cli_bench::PublicationRecord =
        serde_json::from_slice(&assertion.get_output().stdout)?;
    if projected.comparison
        != Some(cli_bench::ComparisonSelection {
            baseline: cli_bench::Role::Candidate,
            candidate: cli_bench::Role::Previous,
        })
        || projected.manifest.roles != record.manifest.roles
        || !projected.analysis.cases.iter().all(|case| {
            case.comparisons.iter().all(|comparison| {
                comparison.candidate == cli_bench::Role::Previous
                    && comparison.baseline == cli_bench::Role::Candidate
            })
        })
    {
        return Err("offline comparison changed role selection or identities".into());
    }
    Ok(())
}

#[allow(dead_code, reason = "CLI uses only the shared engine fixture")]
#[path = "common/timing.rs"]
mod timing_support;

#[test]
fn offline_report_reads_sealed_incomplete_evidence_while_measurement_lock_is_held()
-> Result<(), Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    let store = cli_bench::Store::open(&root.join("evidence"))?;
    let suite = cli_bench::parse_suite(include_str!("inputs/minimal-suite.toml"))?;
    let bundle = store
        .begin_run(&suite)?
        .record_failure(cli_bench::RunOutcome::Incomplete, "RSS unavailable")?;
    let _held =
        cli_bench::MeasurementLock::acquire(&std::sync::atomic::AtomicBool::new(false), || Ok(()))?;
    let assertion = cargo_bin_cmd!()
        .args(["report", "-i"])
        .arg(&bundle.path)
        .args(["-f", "json"])
        .env("PATH", "")
        .env("NO_COLOR", "1")
        .timeout(std::time::Duration::from_secs(3))
        .assert()
        .success()
        .stderr("");
    let record: cli_bench::PublicationRecord =
        serde_json::from_slice(&assertion.get_output().stdout)?;
    if record.result.outcome != cli_bench::RunOutcome::Incomplete {
        return Err("report hid incomplete outcome".into());
    }
    Ok(())
}
