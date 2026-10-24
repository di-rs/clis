use cli_testing::{Command, cargo_bin_cmd};
use predicates::prelude::*;

fn assert_silent_status(cmd: &mut Command, status: i32) {
    cmd.assert()
        .stdout(predicate::str::is_empty())
        .stderr(predicate::str::is_empty())
        .code(status);
}

#[test]
fn false_exits_one_silently() {
    assert_silent_status(&mut cargo_bin_cmd!("false"), 1);
}

#[test]
fn false_ignores_arguments() {
    assert_silent_status(cargo_bin_cmd!("false").args(["--help", "ignored"]), 1);
}

#[test]
fn intentional_false_status_is_persisted_without_error() -> Result<(), Box<dyn std::error::Error>> {
    let logs = assert_fs::TempDir::new()?;
    cargo_bin_cmd!("false")
        .env("SANCTUARY_LOG_DIR", logs.path())
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_STDERR", "off")
        .assert()
        .code(1)
        .stdout("")
        .stderr("");
    let mut records: Vec<serde_json::Value> = Vec::new();
    for entry in std::fs::read_dir(logs.path())? {
        let path = entry?.path();
        if path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().starts_with("false_"))
            && path
                .extension()
                .is_some_and(|extension| extension == "jsonl")
        {
            for line in std::fs::read_to_string(path)?.lines() {
                records.push(serde_json::from_str(line)?);
            }
        }
    }
    if !(records
        .iter()
        .any(|record| record["kv"]["event"] == "command_started"))
    {
        return Err("missing startup record".into());
    }
    if !(records.iter().any(|record| {
        record["kv"]["event"] == "command_finished" && record["kv"]["exit_code"] == 1
    })) {
        return Err("missing intentional status completion".into());
    }
    if records.iter().any(|record| record["level"] == "ERROR") {
        return Err("unexpected error event for intentional false status".into());
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
    if value["command"]["args"] != serde_json::json!([])
        || value["command"]["subcommands"] != serde_json::json!([])
    {
        return Err("utility must have an empty interface".into());
    }
    cli_testing::cargo_bin_cmd!()
        .env_remove("SANCTUARY_CLI_SCHEMA")
        .arg("--help")
        .assert()
        .code(1)
        .stdout("")
        .stderr("");
    cli_testing::cargo_bin_cmd!()
        .env("SANCTUARY_CLI_SCHEMA", "1")
        .args(["--help", "ignored"])
        .assert()
        .code(1)
        .stdout("")
        .stderr("");
    Ok(())
}
