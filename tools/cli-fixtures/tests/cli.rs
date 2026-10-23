#![allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify subprocess contracts; Result propagates setup failures."
)]

use assert_fs::TempDir;
use cli_testing::{Context, Result, cargo_bin_cmd, test_case};
use predicates::prelude::*;
use std::fs;

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

#[test]
fn usage_and_argument_errors() {
    cargo_bin_cmd!()
        .arg("--help")
        .assert()
        .code(0)
        .stdout(predicate::str::contains("record"))
        .stderr("");
    cargo_bin_cmd!()
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicate::str::contains("--suite"));
}

#[test_case("custom_version")]
fn automatically_runs_after_customization(cx: &mut Context) -> Result<()> {
    cx.env("TZ", "UTC")?;
    cx.workspace()?;
    Ok(())
}

#[test_case("custom_version")]
fn accepts_unit_return(cx: &mut Context) {
    assert!(cx.env("TZ", "UTC").is_ok());
}

#[test_case("custom_version")]
fn allows_early_output_assertions(cx: &mut Context) -> Result<()> {
    assert_eq!(cx.run()?.stdout, b"cli-fixtures 0.1.0\n");
    assert_eq!(cx.run()?.stdout, b"cli-fixtures 0.1.0\n");
    Ok(())
}

#[test_case("custom_version")]
#[ignore = "Failure probe executed by attribute_failures_are_native_test_failures"]
fn customization_error(cx: &mut Context) -> Result<()> {
    cx.env("TZ", "UTC")?;
    Err(cli_testing::Error::new("intentional customization error"))
}

#[test_case("custom_version")]
#[ignore = "Failure probe executed by attribute_failures_are_native_test_failures"]
fn customization_panic(cx: &mut Context) {
    assert!(cx.env("TZ", "UTC").is_ok());
    std::panic::resume_unwind(Box::new("intentional customization panic"));
}

#[test_case("unknown_case")]
#[ignore = "Failure probe executed by attribute_failures_are_native_test_failures"]
fn unknown_recipe(cx: &mut Context) {
    assert!(cx.env("TZ", "UTC").is_ok());
}

#[test_case("custom_version")]
#[cfg(any())]
fn excluded_by_cfg(cx: &mut Context) {
    deliberately_unresolved_when_disabled(cx);
}

#[test]
fn attribute_failures_are_native_test_failures() -> TestResult {
    for name in [
        "customization_error",
        "customization_panic",
        "unknown_recipe",
    ] {
        cli_testing::command(std::env::current_exe()?, env!("CARGO_MANIFEST_DIR"))
            .args(["--ignored", "--exact", name])
            .assert()
            .code(101)
            .stdout(predicate::str::contains("1 failed"));
    }
    Ok(())
}

#[cfg(unix)]
fn reference_package(expected: &[u8]) -> TestResult<TempDir> {
    let dir = TempDir::new()?;
    fs::create_dir_all(dir.path().join("tests/inputs"))?;
    fs::create_dir_all(dir.path().join("tests/expected"))?;
    fs::write(dir.path().join("Cargo.toml"), "[package]\nname = 'probe'\n")?;
    fs::write(dir.path().join("tests/inputs/raw"), b"\xff\0\r\nlast")?;
    fs::write(dir.path().join("tests/expected/raw"), expected)?;
    fs::write(
        dir.path().join("tests/cases.toml"),
        format!(
            r#"
schema = 1
binary = "probe"
[references.shell]
program = "/bin/sh"
platform = "{}"
[[cases]]
name = "raw"
args = []
inputs = ["inputs"]
stdin = {{ mode = "pipe", files = ["inputs/raw"] }}
origin = {{ reference = "shell", args = ["-c", "cat; exit 1"], exit = 1, reason = "The controlled reference probe exits 1; the candidate cat exits 0." }}
expect = {{ exit = 0, stdout_file = "expected/raw", stderr = "" }}
[[cases]]
name = "native"
execution = "rust"
args = []
stdin = {{ mode = "file", path = "inputs/raw" }}
origin = {{ reference = "shell", args = ["-c", "cat"], reason = "Controlled reference probe through regular-file stdin." }}
expect = {{ exit = 0, stdout_file = "expected/raw", stderr = "" }}
"#,
            std::env::consts::OS
        ),
    )?;
    Ok(dir)
}

#[cfg(unix)]
fn fixture_command(dir: &TempDir) -> cli_testing::Command {
    let mut command = cargo_bin_cmd!();
    command
        .arg("--suite")
        .arg(dir.path().join("tests/cases.toml"));
    command
}

#[test]
#[cfg(unix)]
fn inspection_and_comparison_expose_ownership_and_reference_differences() -> TestResult {
    let dir = reference_package(b"\xff\0\r\nlast")?;
    fixture_command(&dir)
        .arg("list")
        .assert()
        .code(0)
        .stdout(predicate::str::contains("native [rust]"))
        .stderr("");
    fixture_command(&dir)
        .args(["explain", "raw"])
        .assert()
        .code(0)
        .stdout(predicate::str::contains("candidate cat exits 0"))
        .stderr("");
    fixture_command(&dir)
        .arg("validate")
        .assert()
        .code(0)
        .stderr("");
    fixture_command(&dir)
        .args(["compare", "--candidate", "/bin/cat"])
        .assert()
        .code(0)
        .stdout(
            predicate::str::contains("raw: reference and candidate agree").and(
                predicate::str::contains("native: requires native Rust tests"),
            ),
        )
        .stderr("");
    fs::write(dir.path().join("tests/expected/orphan"), b"unused")?;
    fixture_command(&dir)
        .arg("validate")
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("unreferenced expected files"));
    Ok(())
}

#[test]
#[cfg(unix)]
fn recording_stages_raw_bytes_then_explicit_apply_promotes_them() -> TestResult {
    let dir = reference_package(b"old expectation")?;
    let stage = dir.path().join("recorded");
    let forbidden = dir.path().join("tests/recorded");
    fixture_command(&dir)
        .args(["record", "--output"])
        .arg(&forbidden)
        .assert()
        .code(1)
        .stderr(predicate::str::contains("outside"));
    assert!(!forbidden.exists());
    fixture_command(&dir)
        .args(["record", "--output"])
        .arg(&stage)
        .assert()
        .code(0)
        .stderr("");
    assert_eq!(
        fs::read(dir.path().join("tests/expected/raw"))?,
        b"old expectation"
    );
    assert_eq!(fs::read(stage.join("raw.stdout"))?, b"\xff\0\r\nlast");
    assert_eq!(fs::read(stage.join("native.stdout"))?, b"\xff\0\r\nlast");
    let manifest = fs::read_to_string(stage.join("recording.json"))?;
    fixture_command(&dir)
        .args(["record", "--output"])
        .arg(&stage)
        .assert()
        .code(1)
        .stderr(predicate::str::contains("new output directory"));
    assert_eq!(fs::read_to_string(stage.join("recording.json"))?, manifest);
    for field in [
        "sha256",
        "os_release",
        "LC_ALL",
        "inputs/raw",
        "catalog_sha256",
        "exit",
    ] {
        assert!(manifest.contains(field));
    }
    fixture_command(&dir)
        .arg("apply")
        .arg(&stage)
        .assert()
        .code(0)
        .stdout(predicate::str::contains("1 fixture files changed"))
        .stderr("");
    assert_eq!(
        fs::read(dir.path().join("tests/expected/raw"))?,
        b"\xff\0\r\nlast"
    );
    // A changed destination invalidates the old recording, even on a repeated apply.
    fixture_command(&dir)
        .arg("apply")
        .arg(&stage)
        .assert()
        .code(1)
        .stderr(predicate::str::contains("stale recording"));
    Ok(())
}

#[test]
#[cfg(unix)]
fn stale_or_tampered_recordings_leave_expectations_untouched() -> TestResult {
    for changed in [
        "tests/inputs/raw",
        "tests/inputs/new-empty-dir",
        "tests/cases.toml",
        "recorded/raw.stdout",
        "recorded/recording.json",
    ] {
        let dir = reference_package(b"old")?;
        let stage = dir.path().join("recorded");
        fixture_command(&dir)
            .args(["record", "--output"])
            .arg(&stage)
            .assert()
            .code(0);
        let path = dir.path().join(changed);
        if changed.ends_with("new-empty-dir") {
            fs::create_dir(path)?;
        } else if changed.ends_with("cases.toml") {
            fs::write(
                &path,
                format!("{}\n# changed\n", fs::read_to_string(&path)?),
            )?;
        } else if changed.ends_with("recording.json") {
            fs::remove_file(path)?;
        } else {
            fs::write(path, b"changed")?;
        }
        fixture_command(&dir)
            .arg("apply")
            .arg(&stage)
            .assert()
            .code(1)
            .stderr(predicate::str::is_empty().not());
        assert_eq!(fs::read(dir.path().join("tests/expected/raw"))?, b"old");
    }
    Ok(())
}

#[test]
#[cfg(unix)]
fn failed_references_and_conflicting_shared_writes_never_complete() -> TestResult {
    for script in [
        "cat; exit 7",
        "cat; printf diagnostic >&2; exit 1",
        "printf conflicting; exit 1",
    ] {
        let dir = reference_package(b"old")?;
        let catalog = dir.path().join("tests/cases.toml");
        fs::write(
            &catalog,
            fs::read_to_string(&catalog)?.replace("cat; exit 1", script),
        )?;
        let stage = dir.path().join("recorded");
        fixture_command(&dir)
            .args(["record", "--output"])
            .arg(&stage)
            .assert()
            .code(1)
            .stderr(predicate::str::is_empty().not());
        assert!(!stage.join("recording.json").exists());
        assert_eq!(fs::read(dir.path().join("tests/expected/raw"))?, b"old");
        fixture_command(&dir)
            .arg("apply")
            .arg(&stage)
            .assert()
            .code(1)
            .stderr(predicate::str::contains("incomplete"));
    }
    Ok(())
}

#[test]
#[cfg(unix)]
fn shared_fixtures_preserve_expectations_without_recorded_replacements() -> TestResult {
    let raw = b"\xff\0\r\nlast";
    for owner in [
        r#"origin = { manual = "Independently specified stdout." }
expect = { exit = 0, stdout_file = "expected/raw", stderr = "" }"#,
        r#"origin = { manual = "Independently specified stderr." }
expect = { exit = 0, stdout = "", stderr_file = "expected/raw" }"#,
        r#"origin = { reference = "shell", args = ["-c", ":"], stderr = "", reason = "The candidate has a different stderr contract." }
expect = { exit = 0, stdout = "", stderr_file = "expected/raw" }"#,
    ] {
        for expected in [b"old".as_slice(), raw.as_slice()] {
            let dir = reference_package(expected)?;
            let catalog = dir.path().join("tests/cases.toml");
            fs::write(
                &catalog,
                format!(
                    "{}\n[[cases]]\nname = \"protected\"\nargs = []\n{owner}\n",
                    fs::read_to_string(&catalog)?
                ),
            )?;
            let stage = dir.path().join("recorded");
            let mut command = fixture_command(&dir);
            command.args(["record", "--output"]).arg(&stage);
            if expected == raw {
                command.assert().code(0).stderr("");
                fixture_command(&dir)
                    .arg("apply")
                    .arg(&stage)
                    .assert()
                    .code(0)
                    .stdout(predicate::str::contains("0 fixture files changed"))
                    .stderr("");
            } else {
                command.assert().code(1).stderr(predicate::str::contains(
                    "conflicts with preserved expectation",
                ));
                assert!(!stage.join("recording.json").exists());
            }
            assert_eq!(fs::read(dir.path().join("tests/expected/raw"))?, expected);
        }
    }
    Ok(())
}

#[test]
#[cfg(unix)]
fn recording_and_apply_reject_a_stale_loaded_catalog() -> TestResult {
    let dir = reference_package(b"old")?;
    let catalog = dir.path().join("tests/cases.toml");
    let original = format!(
        r#"schema = 1
binary = "probe"
[references.shell]
program = "/bin/sh"
platform = "{}"
[[cases]]
name = "inline"
args = []
stdin = {{ mode = "pipe", text = "old" }}
origin = {{ reference = "shell", args = ["-c", "cat"], reason = "Controlled stdin reference." }}
expect = {{ exit = 0, stdout_file = "expected/raw", stderr = "" }}
"#,
        std::env::consts::OS
    );
    fs::write(&catalog, &original)?;
    let loaded = cli_testing::catalog::Suite::load(&catalog)?;
    fs::write(
        &catalog,
        original.replace("text = \"old\"", "text = \"new\""),
    )?;
    let stage = dir.path().join("recorded");
    assert!(
        cli_testing::recording::record(&loaded, &stage)
            .is_err_and(|error| error.to_string().contains("stale catalog"))
    );
    assert!(!stage.exists());

    let current = cli_testing::catalog::Suite::load(&catalog)?;
    cli_testing::recording::record(&current, &stage)?;
    assert!(
        cli_testing::recording::apply(&loaded, &stage)
            .is_err_and(|error| error.to_string().contains("stale catalog"))
    );
    assert_eq!(fs::read(dir.path().join("tests/expected/raw"))?, b"old");
    cli_testing::recording::apply(&current, &stage)?;
    assert_eq!(fs::read(dir.path().join("tests/expected/raw"))?, b"new");
    Ok(())
}
