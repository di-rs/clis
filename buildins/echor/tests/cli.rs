use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;

#[test]
fn dies_no_args() {
    let mut cmd = cargo_bin_cmd!();
    cmd.assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("Usage"));
}

#[test]
fn runs() {
    let mut cmd = cargo_bin_cmd!();
    cmd.arg("Hello")
        .assert()
        .success()
        .stdout("Hello\n")
        .stderr("");
}

fn run(args: &[&str], expected_file: &str) -> Result<(), std::io::Error> {
    let expected = cli_testing::fixtures!().read_expected(expected_file)?;
    cargo_bin_cmd!()
        .args(args)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
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
fn rejects_unknown_flag() {
    cargo_bin_cmd!()
        .arg("--unknown")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("--unknown"));
}

#[test]
fn empty_arguments_keep_separators() {
    for (args, expected) in [
        (vec![""], "\n"),
        (vec!["-n", ""], ""),
        (vec!["", "é", ""], " é \n"),
    ] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn end_of_options_preserves_flag_like_text() {
    cargo_bin_cmd!()
        .args(["--", "-n", "--help"])
        .assert()
        .success()
        .stdout("-n --help\n")
        .stderr("");
}

#[test]
fn unicode_and_backslashes_are_literal() {
    cargo_bin_cmd!()
        .args(["-n", "世界", r"\n\t"])
        .assert()
        .success()
        .stdout("世界 \\n\\t")
        .stderr("");
}

#[test]
fn hello1() -> Result<(), std::io::Error> {
    run(&["Hello there"], "hello1.txt")
}

#[test]
fn hello2() -> Result<(), std::io::Error> {
    run(&["Hello", "there"], "hello2.txt")
}

#[test]
fn hello1n() -> Result<(), std::io::Error> {
    run(&["-n", "Hello there"], "hello1.n.txt")
}

#[test]
fn hello2n() -> Result<(), std::io::Error> {
    run(&["-n", "Hello", "there"], "hello2.n.txt")
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
