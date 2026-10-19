#![cfg(unix)]

use assert_fs::{TempDir, prelude::*};
use cli_testing::{Fixtures, command};
use std::io;

type Result = std::result::Result<(), Box<dyn std::error::Error>>;

#[test]
fn stdin_and_outputs_preserve_all_bytes() -> Result {
    let dir = TempDir::new()?;
    let bytes = b"a\0\xff\r\nunterminated";
    dir.child("inputs/data").write_binary(bytes)?;
    dir.child("expected/data").write_binary(bytes)?;
    let fixtures = Fixtures::new(dir.path());
    let input = fixtures.read_input("data")?;
    let expected = fixtures.read_expected("data")?;
    command("/bin/cat", dir.path())
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions check fixture errors; Result propagates setup I/O errors."
)]
fn missing_fixtures_report_the_path() -> Result {
    let dir = TempDir::new()?;
    let error = Fixtures::new(dir.path())
        .read_expected("absent")
        .err()
        .ok_or("missing fixture unexpectedly read")?;
    assert_eq!(error.kind(), io::ErrorKind::NotFound);
    assert!(error.to_string().contains("expected/absent"));
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions check fixture errors; Result propagates setup I/O errors."
)]
fn fixture_names_cannot_escape_their_directory() -> Result {
    let dir = TempDir::new()?;
    let fixtures = Fixtures::new(dir.path());
    for name in ["", "/tmp/file", "../file", "nested/../../file"] {
        let error = fixtures
            .read_input(name)
            .err()
            .ok_or("invalid fixture name accepted")?;
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    }
    Ok(())
}

#[test]
fn command_defaults_are_deterministic_and_overridable() -> Result {
    let dir = TempDir::new()?;
    command("/bin/sh", dir.path())
        .args(["-c", "printf '%s|' \"$LC_ALL\" \"$TZ\" \"$NO_COLOR\" \"$TERM\" \"$SANCTUARY_LOG_LEVEL\" \"$SANCTUARY_LOG_STDERR\"; pwd -P"])
        .assert().code(0).stderr("").stdout(format!(
            "C|UTC|1|dumb|off|off|{}\n", dir.path().canonicalize()?.display()
        ));
    command("/bin/sh", dir.path())
        .env("TZ", "Europe/Paris")
        .args(["-c", "printf '%s' \"$TZ\""])
        .assert()
        .code(0)
        .stderr("")
        .stdout("Europe/Paris");
    Ok(())
}
