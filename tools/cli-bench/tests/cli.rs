use assert_cmd::cargo::cargo_bin_cmd;
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
fn valid_run_selection_does_not_claim_unimplemented_execution() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .args(["run", "-s", "tailr", "-a", "/candidate", "-x", "/reference"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("execution is not implemented"));
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
