use assert_cmd::cargo::cargo_bin_cmd;
use assert_fs::{TempDir, prelude::*};
use predicates::prelude::*;
use std::{fs, path::Path};

fn command() -> assert_cmd::Command {
    let mut command = cargo_bin_cmd!();
    for key in [
        "CLIS_LOG_LEVEL",
        "CLIS_LOG_FORMAT",
        "CLIS_LOG_FILE",
        "CLIS_TIMINGS",
    ] {
        command.env_remove(key);
    }
    command
}

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions fail the test; Result propagates fixture I/O errors."
)]
fn assert_text(path: &Path, lines: usize) -> Result<()> {
    let text = fs::read_to_string(path)?;
    assert_eq!(text.lines().count(), lines);
    assert!(text.ends_with('\n'));
    for line in text.lines() {
        let words: Vec<_> = line.split(' ').collect();
        assert!((7..15).contains(&words.len()));
        for word in words {
            assert!((2..12).contains(&word.len()));
            assert!(word.bytes().all(|byte| byte.is_ascii_alphanumeric()));
        }
    }
    Ok(())
}

#[test]
fn usage() {
    for flag in ["-h", "--help"] {
        command()
            .arg(flag)
            .assert()
            .success()
            .stdout(predicate::str::contains("Usage"))
            .stderr("");
    }
}

#[test]
fn creates_default_output_file() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["-n", "1"])
        .assert()
        .success()
        .stdout(predicate::str::contains("out.txt"))
        .stderr("");
    assert_text(&dir.path().join("out.txt"), 1)
}

#[test]
fn creates_requested_line_counts() -> Result<()> {
    let dir = TempDir::new()?;
    for (flag, count, lines) in [("-n", "1", 1), ("--lines", "8", 8)] {
        let file = dir.child(format!("{count}.txt"));
        command()
            .arg(file.path())
            .args([flag, count])
            .assert()
            .success()
            .stdout(predicate::str::contains(format!("wrote {count} line")))
            .stderr("");
        assert_text(file.path(), lines)?;
    }
    Ok(())
}

#[test]
fn replaces_existing_output() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("output.txt");
    file.write_str(&"old contents\n".repeat(100))?;
    command()
        .arg(file.path())
        .args(["-n", "2"])
        .assert()
        .success()
        .stderr("");
    assert_text(file.path(), 2)
}

#[test]
fn invalid_counts_do_not_modify_output() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("output.txt");
    file.write_str("keep me\n")?;
    for count in ["0", "-1", "abc", "18446744073709551616"] {
        command()
            .arg(file.path())
            .arg(format!("--lines={count}"))
            .assert()
            .code(2)
            .stdout("")
            .stderr(predicate::str::contains("error:"));
        file.assert("keep me\n");
    }
    Ok(())
}

#[test]
fn reports_output_path_errors() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("directory").create_dir_all()?;
    dir.child("file").write_str("keep me")?;
    for path in ["directory", "missing/output.txt", "file/output.txt"] {
        command()
            .current_dir(dir.path())
            .args([path, "-n", "1"])
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains(format!(
                "Cannot create file {path}"
            )));
    }
    dir.child("file").assert("keep me");
    dir.child("missing").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn explicit_level_overrides_environment_and_aliases() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["-n", "1", "-vv", "--log-level=off"])
        .env("CLIS_LOG_LEVEL", "invalid")
        .assert()
        .success()
        .stderr("");
    command()
        .current_dir(dir.path())
        .args(["-n", "1", "-q", "--log-level=info"])
        .assert()
        .success()
        .stderr(predicate::str::contains("generated"));
    Ok(())
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify captured records; Result propagates fixture errors."
)]
#[test]
fn structured_timings_do_not_require_verbose_logging() -> Result<()> {
    let dir = TempDir::new()?;
    let output = command()
        .current_dir(dir.path())
        .args(["-n", "2", "--timings", "--log-format=json"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Done, wrote 2 lines"))
        .get_output()
        .clone();
    let stderr = String::from_utf8(output.stderr)?;
    let mut stages = Vec::new();
    for line in stderr.lines() {
        let record: serde_json::Value = serde_json::from_str(line)?;
        assert_eq!(record.get("kind"), Some(&serde_json::json!("timing")));
        stages.push(record);
    }
    assert!(stages.iter().any(|record| record.get("stage")
        == Some(&serde_json::json!("generate"))
        && record.pointer("/fields/lines_written") == Some(&serde_json::json!(2))));
    Ok(())
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify captured records; Result propagates fixture errors."
)]
#[test]
fn environment_logging_can_be_redirected_and_disabled_explicitly() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["-n", "1", "--log-file=events.jsonl"])
        .env("CLIS_LOG_LEVEL", "info")
        .env("CLIS_LOG_FORMAT", "json")
        .assert()
        .success()
        .stderr("");
    let text = fs::read_to_string(dir.path().join("events.jsonl"))?;
    let record: serde_json::Value = serde_json::from_str(text.trim())?;
    assert_eq!(record.get("level"), Some(&serde_json::json!("INFO")));
    command()
        .current_dir(dir.path())
        .args([
            "-n",
            "1",
            "--log-level=off",
            "--timings=false",
            "--log-file=-",
        ])
        .env("CLIS_LOG_FILE", "events.jsonl")
        .env("CLIS_TIMINGS", "true")
        .assert()
        .success()
        .stderr("");
    Ok(())
}

#[test]
fn invalid_diagnostics_do_not_create_data() -> Result<()> {
    let dir = TempDir::new()?;
    for key in ["CLIS_LOG_LEVEL", "CLIS_LOG_FORMAT", "CLIS_TIMINGS"] {
        command()
            .current_dir(dir.path())
            .args(["output", "-n", "1"])
            .env(key, "invalid")
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains(key));
        dir.child("output").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn rejects_existing_or_overlapping_log_destinations() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("existing").write_str("keep me")?;
    for destination in ["existing", "output", "missing/events"] {
        command()
            .current_dir(dir.path())
            .args([
                "output",
                "-n",
                "1",
                "--log-level=info",
                "--log-file",
                destination,
            ])
            .assert()
            .failure()
            .stdout("");
    }
    dir.child("existing").assert("keep me");
    dir.child("output").assert("");
    Ok(())
}

#[cfg(unix)]
#[test]
fn refuses_symlink_alias_between_data_and_new_log() -> Result<()> {
    let dir = TempDir::new()?;
    std::os::unix::fs::symlink("events", dir.path().join("data"))?;
    command()
        .current_dir(dir.path())
        .args(["data", "-n", "1", "--log-file=events"])
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("same file"));
    dir.child("events").assert("");
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn buffered_output_failure_is_not_success() {
    command()
        .args(["/dev/full", "-n", "1"])
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("flush"));
}

#[test]
fn rejects_undocumented_log_levels_before_creating_output() -> Result<()> {
    let dir = TempDir::new()?;
    for level in ["", "0", "5", "invalid"] {
        command()
            .current_dir(dir.path())
            .args(["-n", "1"])
            .arg(format!("--log-level={level}"))
            .assert()
            .code(2)
            .stdout("");
        dir.child("out.txt").assert(predicate::path::missing());
    }
    Ok(())
}
