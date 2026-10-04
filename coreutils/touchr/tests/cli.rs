use assert_cmd::{Command, cargo::cargo_bin_cmd};
use assert_fs::{TempDir, prelude::*};
use predicates::prelude::*;
use std::{
    fs,
    path::Path,
    time::{Duration, UNIX_EPOCH},
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn command(dir: &TempDir) -> Command {
    let mut cmd = cargo_bin_cmd!();
    cmd.current_dir(dir.path()).env("TZ", "UTC");
    cmd
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions fail the test; Result propagates fixture I/O errors."
)]
fn assert_modified(path: &Path, seconds: u64) -> Result<()> {
    assert_eq!(
        fs::metadata(path)?.modified()?.duration_since(UNIX_EPOCH)?,
        Duration::from_secs(seconds)
    );
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
fn creates_single_file() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .arg("new")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("new")
        .assert(predicate::path::is_file())
        .assert("");
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions fail the test; Result propagates fixture I/O errors."
)]
fn preserves_contents_and_updates_existing_file() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("existing");
    file.write_str("keep these contents\n")?;
    fs::File::open(file.path())?.set_modified(UNIX_EPOCH)?;
    command(&dir)
        .arg("existing")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    file.assert("keep these contents\n");
    assert!(fs::metadata(file.path())?.modified()? > UNIX_EPOCH);
    Ok(())
}

#[test]
fn sets_supported_numeric_timestamps_on_multiple_files() -> Result<()> {
    let dir = TempDir::new()?;
    for (timestamp, seconds) in [
        ("200001010000", 946_684_800),
        ("20000101000007", 946_684_807),
    ] {
        dir.child("existing").write_str("preserve me")?;
        let new = dir.child(format!("new-{timestamp}"));
        command(&dir)
            .args(["-t", timestamp, "existing"])
            .arg(new.path())
            .assert()
            .success()
            .stdout("")
            .stderr("");
        dir.child("existing").assert("preserve me");
        new.assert("");
        assert_modified(dir.child("existing").path(), seconds)?;
        assert_modified(new.path(), seconds)?;
    }
    Ok(())
}

#[test]
fn c_does_not_create_missing_files() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["-c", "missing", "also-missing"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    for name in ["missing", "also-missing"] {
        dir.child(name).assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn c_still_updates_existing_files() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("existing").write_str("keep me")?;
    command(&dir)
        .args(["-c", "-t", "20000101000007", "missing", "existing"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("missing").assert(predicate::path::missing());
    dir.child("existing").assert("keep me");
    assert_modified(dir.child("existing").path(), 946_684_807)
}

#[test]
fn rejects_invalid_timestamps_without_creating_files() -> Result<()> {
    let dir = TempDir::new()?;
    for timestamp in ["bad", "202413010000", "202402300000"] {
        command(&dir)
            .args(["-t", timestamp, "new"])
            .assert()
            .code(2)
            .stdout("")
            .stderr(predicate::str::contains("Invalid timestamp format"));
        dir.child("new").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn reports_creation_path_errors() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("file").write_str("keep me")?;
    for path in ["missing/child", "file/child"] {
        command(&dir)
            .arg(path)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains(format!(
                "failed to create file {path}"
            )));
    }
    dir.child("file").assert("keep me");
    dir.child("missing").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn requires_file_operand() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::is_empty().not());
    Ok(())
}
