#[cfg(unix)]
#[test]
fn startup_without_a_controlling_terminal_reports_an_error() {
    // Crossterm can use /dev/tty even with piped stdin. Leave interactive terminal
    // checks to PTY/manual verification; this scenario with a TTY could hang.
    if std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty")
        .is_ok()
    {
        eprintln!("skipping non-TTY startup check: a controlling terminal is attached");
        return;
    }

    cli_testing::cargo_bin_cmd!()
        .write_stdin("")
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("os error"));
}

#[test]
fn discovery_does_not_need_a_terminal() {
    for flag in ["--help", "--version"] {
        cli_testing::cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .code(0)
            .stdout(predicates::str::contains("kara"))
            .stderr("");
    }
    cli_testing::cargo_bin_cmd!()
        .args(["one", "two"])
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicates::str::contains("unexpected argument"));
}

#[cfg(unix)]
#[test]
fn filenames_cannot_inject_terminal_commands() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/terminal.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .env("CLI_LOG", "off")
        .output()
        .expect("run PTY regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn saves_preserve_permissions_and_editor_state() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/pty_smoke.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY save regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
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
