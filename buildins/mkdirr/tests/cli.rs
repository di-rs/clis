use assert_fs::{TempDir, prelude::*};
use cli_testing::{Command, cargo_bin_cmd};
use predicates::prelude::*;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn command(dir: &TempDir) -> Command {
    let mut cmd = cargo_bin_cmd!();
    cmd.current_dir(dir.path());
    cmd
}

#[test]
fn usage() {
    for flag in ["-h", "--help"] {
        cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .success()
            .stdout(predicate::str::contains("Usage"))
            .stderr("");
    }
}

#[test]
fn creates_single_directory() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .arg("new")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("new").assert(predicate::path::is_dir());
    Ok(())
}

#[test]
fn creates_multiple_directories() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["one", "two", "three"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    for name in ["one", "two", "three"] {
        dir.child(name).assert(predicate::path::is_dir());
    }
    Ok(())
}

#[test]
fn creates_nested_directories_by_default() -> Result<()> {
    // mkdirr currently defaults parent creation to true, unlike standard mkdir.
    let dir = TempDir::new()?;
    command(&dir)
        .arg("one/two/three")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("one/two/three").assert(predicate::path::is_dir());
    Ok(())
}

#[test]
fn creates_nested_directories_with_p() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["-p", "one/two/three"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("one/two/three").assert(predicate::path::is_dir());
    Ok(())
}

#[test]
fn p_accepts_existing_directory() -> Result<()> {
    // Regression: parent creation should tolerate a directory already present.
    let dir = TempDir::new()?;
    dir.child("existing").create_dir_all()?;
    dir.child("existing/contents").write_str("keep me")?;
    command(&dir)
        .args(["-p", "existing"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("existing/contents").assert("keep me");
    Ok(())
}

#[test]
fn rejects_existing_file() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("existing").write_str("keep me")?;
    command(&dir)
        .arg("existing")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("existing"));
    dir.child("existing").assert("keep me");
    Ok(())
}

#[test]
fn rejects_file_as_parent() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("file").write_str("keep me")?;
    command(&dir)
        .arg("file/child")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("file/child"));
    dir.child("file").assert("keep me");
    Ok(())
}

#[test]
fn requires_directory_operand() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::is_empty().not());
    Ok(())
}

#[test]
fn rich_error_is_persisted_with_failing_completion() -> Result<()> {
    let dir = TempDir::new()?;
    let logs = TempDir::new()?;
    dir.child("parent-file").write_str("keep me")?;
    command(&dir)
        .env("SANCTUARY_LOG_DIR", logs.path())
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_STDERR", "off")
        .arg("parent-file/child")
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("parent-file/child"));
    let mut records: Vec<serde_json::Value> = Vec::new();
    for entry in std::fs::read_dir(logs.path())? {
        let path = entry?.path();
        if path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().starts_with("mkdirr_"))
            && path
                .extension()
                .is_some_and(|extension| extension == "jsonl")
        {
            for line in std::fs::read_to_string(path)?.lines() {
                records.push(serde_json::from_str(line)?);
            }
        }
    }
    if !(records.iter().any(|record| {
        record["kv"]["event"] == "error"
            && record["text"]
                .as_str()
                .is_some_and(|message| message.contains("parent-file/child"))
    })) {
        return Err("missing rich error record".into());
    }
    if !(records.iter().any(|record| {
        record["kv"]["event"] == "command_finished" && record["kv"]["exit_code"] == 1
    })) {
        return Err("missing failing completion".into());
    }
    Ok(())
}

#[test]
fn exports_public_cli_schema_without_application_side_effects()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    let logs = assert_fs::TempDir::new()?;
    let output = cli_testing::cargo_bin_cmd!()
        .env("SANCTUARY_CLI_SCHEMA", "1")
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_DIR", logs.path())
        .arg("--help")
        .assert()
        .code(0)
        .stderr("")
        .get_output()
        .stdout
        .clone();
    let value: serde_json::Value = serde_json::from_slice(&output)?;
    if value["schema_version"] != 1 {
        return Err("unexpected schema version".into());
    }
    if value["command"]["name"] != env!("CARGO_PKG_NAME") {
        return Err("unexpected CLI identity".into());
    }
    if !value["command"]["args"].is_array() {
        return Err("missing argument metadata".into());
    }
    if std::fs::read_dir(logs.path())?.count() != 0 {
        return Err("schema export wrote logs".into());
    }
    Ok(())
}
