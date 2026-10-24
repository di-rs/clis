use cli_testing::{Command, cargo_bin_cmd};
use predicates::prelude::*;

fn assert_silent_status(cmd: &mut Command, status: i32) {
    cmd.assert()
        .stdout(predicate::str::is_empty())
        .stderr(predicate::str::is_empty())
        .code(status);
}

#[test]
fn true_exits_zero_silently() {
    assert_silent_status(&mut cargo_bin_cmd!("true"), 0);
}

#[test]
fn true_ignores_arguments() {
    assert_silent_status(cargo_bin_cmd!("true").args(["--help", "ignored"]), 0);
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
        .code(0)
        .stdout("")
        .stderr("");
    cli_testing::cargo_bin_cmd!()
        .env("SANCTUARY_CLI_SCHEMA", "1")
        .args(["--help", "ignored"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    Ok(())
}
