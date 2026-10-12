use assert_cmd::cargo::cargo_bin_cmd;
use assert_fs::{TempDir, prelude::*};
use predicates::prelude::*;
use std::{
    fs,
    path::{Path, PathBuf},
};

fn command() -> assert_cmd::Command {
    let mut command = cargo_bin_cmd!();
    for key in [
        "CLIS_LOG_LEVEL",
        "CLIS_LOG_DIR",
        "CLIS_LOG_STDERR",
        "CLIS_LOG_SESSION",
    ] {
        command.env_remove(key);
    }
    command.env("CLIS_LOG_LEVEL", "off");
    command
}

fn log_files(root: &Path) -> Result<Vec<PathBuf>> {
    fs::read_dir(root)?
        .filter_map(|entry| match entry {
            Ok(entry) => {
                let path = entry.path();
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| {
                        name.starts_with("biggie_") && name.ends_with("_rCURRENT.jsonl")
                    })
                    .then_some(Ok(path))
            }
            Err(error) => Some(Err(error.into())),
        })
        .collect()
}

fn log_records(root: &Path) -> Result<Vec<serde_json::Value>> {
    let file = log_files(root)?
        .into_iter()
        .next()
        .ok_or_else(|| std::io::Error::other("missing Biggie invocation log"))?;
    fs::read_to_string(file)?
        .lines()
        .map(serde_json::from_str)
        .collect::<std::result::Result<_, _>>()
        .map_err(Into::into)
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertion verifies logging opt-out creates no invocation files."
)]
fn assert_no_logs(root: &Path) -> Result<()> {
    assert_eq!(log_files(root)?, Vec::<PathBuf>::new());
    Ok(())
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify persistent parser errors and operation counts."
)]
fn assert_argument_error(root: &Path) -> Result<()> {
    assert!(log_records(root)?.iter().any(|record| {
        record
            .pointer("/kv/event")
            .and_then(serde_json::Value::as_str)
            == Some("argument_error")
    }));
    Ok(())
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertion verifies the operation's completed line count."
)]
fn assert_completed_lines(root: &Path, count: u64) -> Result<()> {
    assert!(log_records(root)?.iter().any(|record| {
        record
            .pointer("/kv/lines_written")
            .and_then(serde_json::Value::as_u64)
            == Some(count)
    }));
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify the command's persistent log contract."
)]
fn command_persists_debug_counters_and_completion_without_stderr() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .env("CLIS_LOG_DIR", dir.path())
        .env("CLIS_LOG_STDERR", "off")
        .env("CLIS_LOG_SESSION", "test-shell")
        .args(["-", "-n", "2", "-w", "0", "-L", "debug"])
        .assert()
        .success()
        .stdout("\n\n")
        .stderr("");
    let records = log_records(dir.path())?;
    assert_eq!(
        records.first().map(|record| &record["kv"]["event"]),
        Some(&serde_json::json!("command_started"))
    );
    let started = records
        .first()
        .ok_or_else(|| std::io::Error::other("missing startup event"))?;
    assert_eq!(started["level"], "INFO");
    assert_eq!(started["kv"]["command"], "biggie");
    assert_eq!(started["kv"]["session_id"], "test-shell");
    let invocation = started["kv"]["invocation_id"]
        .as_str()
        .ok_or_else(|| std::io::Error::other("missing invocation identifier"))?;
    assert_eq!(
        log_files(dir.path())?,
        vec![
            dir.path()
                .join(format!("biggie_{invocation}_rCURRENT.jsonl"))
        ]
    );
    assert!(
        records
            .iter()
            .any(|record| record["level"] == "DEBUG" && record["kv"]["lines_written"] == 2)
    );
    assert_eq!(
        records.last().map(|record| &record["kv"]["event"]),
        Some(&serde_json::json!("command_finished"))
    );
    assert_eq!(
        records.last().map(|record| &record["kv"]["exit_code"]),
        Some(&serde_json::json!(0))
    );
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify argument and runtime errors in persistent logs."
)]
fn failures_persist_errors_and_numeric_completion_by_default() -> Result<()> {
    let dir = TempDir::new()?;
    for (args, event, status) in [
        (vec!["missing/output", "-n", "1"], "error", 1),
        (vec!["--lines=invalid"], "argument_error", 2),
    ] {
        command()
            .current_dir(dir.path())
            .env_remove("CLIS_LOG_LEVEL")
            .env("CLIS_LOG_DIR", dir.path().join(event))
            .args(args)
            .assert()
            .code(status)
            .stdout("");
        let records = log_records(&dir.path().join(event))?;
        assert!(records.iter().any(|record| record["kv"]["event"] == event));
        assert_eq!(
            records.last().map(|record| &record["kv"]["exit_code"]),
            Some(&serde_json::json!(status))
        );
    }
    Ok(())
}

#[test]
fn explicit_off_disables_persistence_even_with_invalid_environment() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .env("CLIS_LOG_LEVEL", "invalid")
        .env("CLIS_LOG_DIR", dir.path())
        .args(["bytes", "-", "-b", "0", "-L", "off"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    assert_no_logs(dir.path())?;
    Ok(())
}

#[test]
fn explicit_off_applies_to_argument_failures() -> Result<()> {
    for args in [
        vec!["bytes", "-", "-b", "invalid", "-L", "off"],
        vec!["-Loff", "bytes", "-", "-b", "invalid"],
        vec!["bytes", "-", "-b", "invalid", "--log-level=off"],
        vec!["bytes", "-", "--unknown", "--log-level", "off"],
        vec!["text", "-", "-w", "invalid", "-NLoff"],
    ] {
        let dir = TempDir::new()?;
        command()
            .env("CLIS_LOG_LEVEL", "info")
            .env("CLIS_LOG_DIR", dir.path())
            .args(args)
            .assert()
            .code(2)
            .stdout("");
        assert_no_logs(dir.path())?;
    }
    Ok(())
}

#[test]
fn explicit_debug_persists_argument_failures_when_environment_is_off() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .env("CLIS_LOG_DIR", dir.path())
        .args(["bytes", "-", "-b", "invalid", "-Ldebug"])
        .assert()
        .code(2)
        .stdout("");
    assert_argument_error(dir.path())?;
    Ok(())
}

#[test]
fn log_level_operands_do_not_override_error_logging() -> Result<()> {
    for args in [
        vec!["bytes", "-b", "invalid", "--", "--log-level=off"],
        vec!["records", "-", "-c", "invalid", "-rLoff"],
        vec!["records", "-", "-c", "invalid", "--record=--log-level=off"],
    ] {
        let dir = TempDir::new()?;
        command()
            .env("CLIS_LOG_LEVEL", "info")
            .env("CLIS_LOG_DIR", dir.path())
            .args(args)
            .assert()
            .code(2)
            .stdout("");
        assert_argument_error(dir.path())?;
    }
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify generation counters for each operation."
)]
fn operation_logs_include_completed_counts() -> Result<()> {
    let dir = TempDir::new()?;
    for (name, args, fields) in [
        (
            "bytes",
            vec!["bytes", "-", "-b", "3"],
            serde_json::json!({"bytes_written":3}),
        ),
        (
            "records",
            vec!["records", "-", "-r", "sample", "-c", "2"],
            serde_json::json!({"records_written":2}),
        ),
        (
            "fields",
            vec!["fields", "-", "-n", "2"],
            serde_json::json!({"records_written":2}),
        ),
        (
            "pair",
            vec![
                "pair", "-l", "left", "-r", "right", "-a", "0", "-j", "2", "-b", "0",
            ],
            serde_json::json!({"left_written":2,"right_written":2}),
        ),
    ] {
        command()
            .current_dir(dir.path())
            .env("CLIS_LOG_DIR", dir.path().join(name))
            .args(args)
            .args(["-L", "debug"])
            .assert()
            .success()
            .stderr("");
        let records = log_records(&dir.path().join(name))?;
        assert!(
            records.iter().any(|record| record["kv"] == fields),
            "missing {name} completion counter"
        );
    }
    Ok(())
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
    for count in ["-1", "abc", "18446744073709551616"] {
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
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions compare seeded output bytes."
)]
fn seeded_output_is_reproducible_across_files_and_stdout() -> Result<()> {
    let dir = TempDir::new()?;
    let first = dir.child("first.txt");
    let second = dir.child("second.txt");
    for file in [&first, &second] {
        command()
            .arg(file.path())
            .args(["-n", "20", "--seed", "42"])
            .assert()
            .success()
            .stderr("");
    }
    let expected = fs::read(first.path())?;
    assert_eq!(fs::read(second.path())?, expected);
    command()
        .current_dir(dir.path())
        .args(["-", "-n", "20", "--seed", "42"])
        .assert()
        .success()
        .stdout(expected)
        .stderr("");
    dir.child("-").assert(predicate::path::missing());
    dir.child("out.txt").assert(predicate::path::missing());
    assert_text(first.path(), 20)
}

#[test]
fn stdout_contains_only_data_even_with_logging() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .env("CLIS_LOG_DIR", dir.path())
        .args([
            "-",
            "-n",
            "2",
            "--words-per-line",
            "0",
            "--log-level",
            "debug",
        ])
        .assert()
        .success()
        .stdout(b"\n\n".as_slice())
        .stderr("");
    Ok(())
}

#[test]
fn closed_stdout_is_an_output_failure() -> Result<()> {
    use assert_cmd::assert::OutputAssertExt;

    let (reader, writer) = std::io::pipe()?;
    drop(reader);
    // assert_cmd::Command replaces stdout with its own pipe; use std for this sink.
    std::process::Command::new(assert_cmd::cargo::cargo_bin!())
        .args(["-", "-n", "1", "--seed", "42", "--log-level=off"])
        .stdout(writer)
        .output()?
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("Cannot flush generated output"));
    Ok(())
}

#[test]
fn zero_lines_creates_empty_output() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("empty.txt");
    file.write_str("old data")?;
    command()
        .arg(file.path())
        .args(["-n", "0", "--seed", "0"])
        .assert()
        .success()
        .stdout(predicate::str::contains("wrote 0 lines"))
        .stderr("");
    file.assert(b"".as_slice());
    command()
        .current_dir(dir.path())
        .args([
            "-",
            "-n",
            "0",
            "--no-final-newline",
            "--line-ending",
            "crlf",
        ])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("-").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn blank_lines_have_exact_requested_terminators() {
    for (ending, no_final, expected) in [
        ("lf", false, b"\n\n\n".as_slice()),
        ("lf", true, b"\n\n".as_slice()),
        ("crlf", false, b"\r\n\r\n\r\n".as_slice()),
        ("crlf", true, b"\r\n\r\n".as_slice()),
    ] {
        let mut cmd = command();
        cmd.args([
            "-",
            "-n",
            "3",
            "--words-per-line",
            "0",
            "--line-ending",
            ending,
        ]);
        if no_final {
            cmd.arg("--no-final-newline");
        }
        cmd.assert().success().stdout(expected).stderr("");
    }
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify generated bytes."
)]
fn fixed_word_shape_and_unterminated_last_line() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("fixed.txt");
    command()
        .arg(file.path())
        .args([
            "-n",
            "2",
            "--seed",
            "18446744073709551615",
            "--words-per-line",
            "3",
            "--word-length",
            "4",
            "--line-ending",
            "crlf",
            "--no-final-newline",
        ])
        .assert()
        .success()
        .stderr("");
    let bytes = fs::read(file.path())?;
    // Each record is 3 * 4 letters + 2 spaces; only the first gets CRLF.
    assert_eq!(bytes.len(), 30);
    assert_eq!(bytes.get(14..16), Some(b"\r\n".as_slice()));
    for line in bytes.split(|&b| b == b'\n') {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        let words: Vec<_> = line.split(|&b| b == b' ').collect();
        assert_eq!(words.len(), 3);
        for word in words {
            assert_eq!(word.len(), 4);
            assert!(word.iter().all(u8::is_ascii_alphanumeric));
        }
    }
    assert!(bytes.last().is_some_and(u8::is_ascii_alphanumeric));
    Ok(())
}

#[test]
fn invalid_generation_options_preserve_existing_and_missing_destinations() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("existing").write_str("keep me")?;
    for option in [
        "--seed=-1",
        "--seed=abc",
        "--seed=18446744073709551616",
        "--words-per-line=4..=2",
        "--words-per-line=-1",
        "--words-per-line=2..4",
        "--words-per-line=4294967296",
        "--word-length=0",
        "--word-length=0..=3",
        "--word-length=8..=2",
        "--word-length=abc",
        "--line-ending=cr",
    ] {
        for destination in ["existing", "missing"] {
            command()
                .current_dir(dir.path())
                .args([destination, "-n", "0", option])
                .assert()
                .code(2)
                .stdout("")
                .stderr(predicate::str::contains("error:"));
        }
        dir.child("existing").assert("keep me");
        dir.child("missing").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn explicit_relative_dash_remains_a_filename() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["./-", "-n", "1", "--words-per-line", "0"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Done, wrote 1 line"))
        .stderr("");
    dir.child("-").assert(b"\n".as_slice());
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
fn explicit_level_overrides_environment() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["-n", "1", "--log-level=off"])
        .env("CLIS_LOG_LEVEL", "invalid")
        .assert()
        .success()
        .stderr("");
    command()
        .current_dir(dir.path())
        .args(["-n", "1"])
        .env("CLIS_LOG_LEVEL", "info")
        .env("CLIS_LOG_DIR", dir.path().join("logs"))
        .assert()
        .success()
        .stderr("");
    Ok(())
}

#[test]
fn debug_verbosity_persists_operation_counts() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .env("CLIS_LOG_DIR", dir.path().join("logs"))
        .args(["-n", "2", "--log-level=debug"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Done, wrote 2 lines"))
        .stderr("");
    assert_completed_lines(&dir.path().join("logs"), 2)?;
    assert_text(&dir.path().join("out.txt"), 2)
}

#[test]
fn invalid_environment_level_does_not_create_data() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["output", "-n", "1"])
        .env("CLIS_LOG_DIR", dir.path().join("logs"))
        .env("CLIS_LOG_LEVEL", "invalid")
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicate::str::contains("--log-level"));
    dir.child("output").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn unsupported_diagnostic_options_do_not_modify_output() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("output").write_str("keep me")?;
    for option in [
        "--log-format=json",
        "--log-file=events",
        "--timings",
        "-v",
        "-q",
    ] {
        command()
            .current_dir(dir.path())
            .args(["output", "-n", "1", option])
            .assert()
            .code(2)
            .stdout("");
        dir.child("output").assert("keep me");
        dir.child("events").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn required_errors_are_reported_once_at_every_level() -> Result<()> {
    let dir = TempDir::new()?;
    for level in ["off", "error", "debug", "trace"] {
        command()
            .current_dir(dir.path())
            .args(["missing/output", "-n", "1"])
            .arg(format!("--log-level={level}"))
            .env("CLIS_LOG_DIR", dir.path().join("logs"))
            .assert()
            .code(1)
            .stdout("")
            .stderr(predicate::str::contains("biggie: Cannot create file missing/output").count(1));
    }
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
            .env("CLIS_LOG_DIR", dir.path().join("logs"))
            .assert()
            .code(2)
            .stdout("");
        dir.child("out.txt").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn default_and_explicit_text_preserve_seeded_ascii() {
    for prefix in [vec![], vec!["text"]] {
        command()
            .args(prefix)
            .args(["-", "-s", "42", "-n", "2", "-w", "2", "-l", "3"])
            .assert()
            .success()
            .stdout("Pi3 ZCn\nvL2 IeA\n")
            .stderr("");
    }
}

#[test]
fn text_custom_alphabet_and_short_flags() {
    command()
        .args([
            "text", "-", "-A", "猫", "-u", "scalars", "-n", "2", "-w", "2", "-l", "2", "-d", "\t",
            "-e", "crlf", "-N", "-s", "0", "-L", "off",
        ])
        .assert()
        .success()
        .stdout("猫猫\t猫猫\r\n猫猫\t猫猫")
        .stderr("");
    command()
        .args([
            "-L",
            "off",
            "text",
            "-",
            "--alphabet-chars",
            "a",
            "--word-separator",
            "records",
            "-n",
            "1",
            "-w",
            "2",
            "-l",
            "1",
        ])
        .assert()
        .success()
        .stdout("arecordsa\n")
        .stderr("");
}

#[test]
fn text_validation_preserves_destinations() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("keep").write_str("original")?;
    for args in [
        vec!["-A", ""],
        vec!["-A", "aa"],
        vec!["-A", "a\n"],
        vec!["-A", "猫"],
        vec!["-a", "unicode"],
        vec!["-A", "a", "-a", "ascii"],
        vec!["-d", "\r"],
        vec!["-r", "x"],
    ] {
        command()
            .current_dir(dir.path())
            .args(["text", "keep", "-n", "0"])
            .args(args)
            .assert()
            .code(2)
            .stdout("");
        dir.child("keep").assert("original");
    }
    command()
        .current_dir(dir.path())
        .args(["-n", "0", "text", "keep"])
        .assert()
        .code(2);
    dir.child("keep").assert("original");
    Ok(())
}

#[test]
fn reserved_names_can_be_text_destinations() -> Result<()> {
    let dir = TempDir::new()?;
    for args in [
        vec!["text", "records", "-n", "0"],
        vec!["./records", "-n", "0"],
        vec!["-n", "0", "--", "records"],
    ] {
        command()
            .current_dir(dir.path())
            .args(args)
            .assert()
            .success()
            .stderr("");
        dir.child("records").assert(b"".as_slice());
    }
    Ok(())
}

#[test]
fn text_help_and_version_do_not_generate_files() -> Result<()> {
    let dir = TempDir::new()?;
    for flag in ["-h", "--help", "-V", "--version"] {
        command()
            .current_dir(dir.path())
            .args(["text", flag])
            .assert()
            .success()
            .stderr("");
        dir.child("out.txt").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn records_schedule_has_exact_runs_and_positions() {
    for args in [
        vec!["-r", "miss", "-r", "Hit", "-r", "", "-p", "2", "-c", "2"],
        vec![
            "--record", "miss", "--record", "Hit", "--record", "", "--repeat", "2", "--cycles", "2",
        ],
    ] {
        command()
            .args(["records", "-"])
            .args(args)
            .assert()
            .success()
            .stdout("miss\nmiss\nHit\nHit\n\n\nmiss\nmiss\nHit\nHit\n\n\n")
            .stderr("");
    }
    command()
        .args([
            "records",
            "-",
            "-r",
            "e\u{301}👨‍👩‍👧",
            "-r",
            "",
            "-e",
            "crlf",
            "-N",
        ])
        .assert()
        .success()
        .stdout("e\u{301}👨‍👩‍👧\r\n")
        .stderr("");
}

#[test]
fn records_files_validate_before_output_and_preserve_aliases() -> Result<()> {
    use std::os::unix::fs::symlink;
    let dir = TempDir::new()?;
    let input = dir.child("input");
    input.write_binary(b"a\r\nb\n")?;
    for flag in ["-f", "--records-file"] {
        command()
            .current_dir(dir.path())
            .args(["records", "-", flag, "input"])
            .assert()
            .success()
            .stdout("a\r\nb\n")
            .stderr("");
    }
    fs::hard_link(input.path(), dir.child("hard").path())?;
    symlink(input.path(), dir.child("link").path())?;
    for output in ["input", "hard", "link"] {
        command()
            .current_dir(dir.path())
            .args(["records", output, "-f", "input"])
            .assert()
            .code(1)
            .stdout("");
        input.assert(b"a\r\nb\n".as_slice());
    }
    input.write_binary(&[0xff])?;
    command()
        .current_dir(dir.path())
        .args(["records", "out", "-f", "input"])
        .assert()
        .code(1);
    dir.child("out").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn records_reject_inapplicable_flags_and_overflow() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("keep").write_str("keep")?;
    for args in [
        vec!["-n", "0"],
        vec!["-s", "1"],
        vec!["-p", "0"],
        vec!["-r", "a\nb"],
        vec!["-r", "a", "-p", "18446744073709551615", "-c", "2"],
    ] {
        command()
            .current_dir(dir.path())
            .args(["records", "keep"])
            .args(args)
            .assert()
            .code(2);
        dir.child("keep").assert("keep");
    }
    Ok(())
}

#[test]
fn fields_preserve_empty_cells() {
    command()
        .args([
            "fields", "-", "-n", "1", "-f", "3", "-v", "a", "-v", "", "-v", "c",
        ])
        .assert()
        .success()
        .stdout("a\t\tc\n")
        .stderr("");
    command()
        .args([
            "fields", "-", "-n", "2", "-f", "3", "-v", "a", "-v", "b", "-v", "c", "-E", "2",
        ])
        .assert()
        .success()
        .stdout("a\t\tc\n\tb\t\n")
        .stderr("");
}

#[cfg(feature = "csv")]
#[test]
fn csv_fields_preserve_quoting_and_embedded_newlines() {
    command()
        .args([
            "fields", "-", "-n", "1", "-F", "csv", "-f", "3", "-d", ",", "-v", "a,b", "-v", "",
            "-v", "a\"b",
        ])
        .assert()
        .success()
        .stdout("\"a,b\",,\"a\"\"b\"\n")
        .stderr("");
    command()
        .args([
            "fields",
            "-",
            "--lines",
            "1",
            "--format",
            "csv",
            "--fields",
            "1",
            "--delimiter",
            ",",
            "--field-value",
            "a\r\n",
            "--line-ending",
            "crlf",
            "--no-final-newline",
        ])
        .assert()
        .success()
        .stdout("\"a\r\n\"")
        .stderr("");
}

#[test]
fn fields_reject_invalid_options_before_truncation() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("keep").write_str("keep")?;
    for args in [
        vec!["-f", "0"],
        vec!["-d", "é"],
        vec!["-d", "\""],
        vec!["-v", "a\tb"],
        vec!["-v", "x\ny"],
        vec!["-v", "x", "-s", "1"],
        vec!["-v", "x", "-l", "2"],
        vec!["-E", "0"],
        vec!["-f", "4294967295", "-l", "4294967295"],
        vec!["-w", "3"],
    ] {
        command()
            .current_dir(dir.path())
            .args(["fields", "keep", "-n", "0"])
            .args(args)
            .assert()
            .code(2);
        dir.child("keep").assert("keep");
    }
    Ok(())
}

#[test]
fn byte_patterns_obey_exact_budget() {
    for args in [
        vec!["-b", "7", "-p", "00ff1b0d0a"],
        vec!["--bytes", "7", "--pattern-hex", "00ff1b0d0a"],
    ] {
        command()
            .args(["bytes", "-"])
            .args(args)
            .assert()
            .success()
            .stdout(vec![0, 255, 27, 13, 10, 0, 255])
            .stderr("");
    }
    command()
        .args(["bytes", "-", "-b", "1", "-p", "0d0a"])
        .assert()
        .success()
        .stdout(vec![13])
        .stderr("");
}

#[test]
fn byte_options_preserve_destinations_on_invalid_input() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("keep").write_str("keep")?;
    for args in [
        vec!["-p", ""],
        vec!["-p", "a"],
        vec!["-p", "zz"],
        vec!["-p", "00", "-s", "1"],
        vec!["-n", "0"],
        vec!["-N"],
        vec!["-e", "lf"],
    ] {
        command()
            .current_dir(dir.path())
            .args(["bytes", "keep", "-b", "0"])
            .args(args)
            .assert()
            .code(2);
        dir.child("keep").assert("keep");
    }
    command()
        .current_dir(dir.path())
        .args(["bytes", "keep"])
        .assert()
        .code(2);
    dir.child("keep").assert("keep");
    Ok(())
}

#[test]
fn pair_streams_are_sorted_with_exact_overlap() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args([
            "pair", "-l", "left", "-r", "right", "-a", "2", "-j", "3", "-b", "1", "-c", "2",
        ])
        .assert()
        .success()
        .stderr("");
    dir.child("left").assert("0000000000000000\n0000000000000000\n0000000000000001\n0000000000000001\n0000000000000002\n0000000000000002\n0000000000000003\n0000000000000003\n0000000000000004\n0000000000000004\n");
    dir.child("right").assert("0000000000000002\n0000000000000002\n0000000000000003\n0000000000000003\n0000000000000004\n0000000000000004\n0000000000000005\n0000000000000005\n");
    Ok(())
}

#[test]
fn pair_rejects_aliases_and_invalid_counts() -> Result<()> {
    use std::os::unix::fs::symlink;
    let dir = TempDir::new()?;
    dir.child("keep").write_str("original")?;
    fs::hard_link(dir.child("keep").path(), dir.child("hard").path())?;
    symlink(dir.child("keep").path(), dir.child("link").path())?;
    for right in ["keep", "./keep", "hard", "link"] {
        command()
            .current_dir(dir.path())
            .args([
                "pair", "-l", "keep", "-r", right, "-a", "1", "-j", "0", "-b", "0",
            ])
            .assert()
            .code(1)
            .stdout("");
        dir.child("keep").assert("original");
    }
    for args in [
        vec!["-a", "18446744073709551615", "-j", "1", "-b", "0"],
        vec!["-a", "0", "-j", "0", "-b", "1", "-c", "0"],
        vec!["-a", "0", "-j", "0", "-b", "0", "-n", "1"],
    ] {
        command()
            .current_dir(dir.path())
            .args(["pair", "-l", "keep", "-r", "other"])
            .args(args)
            .assert()
            .code(2);
        dir.child("keep").assert("original");
        dir.child("other").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn pair_second_destination_failure_reports_partial_effects() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args([
            "pair",
            "-l",
            "left",
            "-r",
            "missing/right",
            "-a",
            "1",
            "-j",
            "0",
            "-b",
            "1",
        ])
        .assert()
        .code(1)
        .stdout("");
    dir.child("left").assert(b"".as_slice());
    dir.child("missing").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn command_help_is_scoped_and_every_command_has_version() -> Result<()> {
    let dir = TempDir::new()?;
    for name in ["text", "records", "fields", "bytes", "pair"] {
        for flag in ["-h", "--help", "-V", "--version"] {
            command()
                .current_dir(dir.path())
                .args([name, flag])
                .assert()
                .success()
                .stderr("");
        }
        dir.child("out.txt").assert(predicate::path::missing());
    }
    command()
        .args(["records", "--help"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("--record")
                .and(predicate::str::contains("--word-length").not()),
        );
    command()
        .args(["bytes", "--help"])
        .assert()
        .success()
        .stdout(predicate::str::contains("--bytes").and(predicate::str::contains("--lines").not()));
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions compare actual command output bytes."
)]
fn short_and_long_flags_produce_identical_streams() -> Result<()> {
    let dir = TempDir::new()?;
    let format = "delimited";
    for (name, short, long) in [
        (
            "text",
            vec![
                "-n", "2", "-s", "42", "-w", "2", "-l", "3", "-a", "unicode", "-u", "scalars",
                "-d", "\u{2003}", "-e", "crlf", "-N",
            ],
            vec![
                "--lines",
                "2",
                "--seed",
                "42",
                "--words-per-line",
                "2",
                "--word-length",
                "3",
                "--alphabet",
                "unicode",
                "--length-unit",
                "scalars",
                "--word-separator",
                "\u{2003}",
                "--line-ending",
                "crlf",
                "--no-final-newline",
            ],
        ),
        (
            "fields",
            vec![
                "-n", "2", "-s", "42", "-f", "3", "-l", "5", "-F", format, "-d", ",", "-E", "2",
                "-e", "crlf", "-N",
            ],
            vec![
                "--lines",
                "2",
                "--seed",
                "42",
                "--fields",
                "3",
                "--word-length",
                "5",
                "--format",
                format,
                "--delimiter",
                ",",
                "--empty-every",
                "2",
                "--line-ending",
                "crlf",
                "--no-final-newline",
            ],
        ),
        (
            "bytes",
            vec!["-b", "16385", "-s", "42"],
            vec!["--bytes", "16385", "--seed", "42"],
        ),
    ] {
        let first = command()
            .args([name, "-"])
            .args(&short)
            .args(["-L", "off"])
            .env("CLIS_LOG_LEVEL", "invalid")
            .assert()
            .success()
            .stderr("")
            .get_output()
            .stdout
            .clone();
        command()
            .args(["--log-level", "off", name, "-"])
            .args(&long)
            .env("CLIS_LOG_LEVEL", "invalid")
            .assert()
            .success()
            .stdout(first.clone())
            .stderr("");
        command()
            .current_dir(dir.path())
            .args([name, "data"])
            .args(long)
            .assert()
            .success()
            .stderr("");
        assert_eq!(fs::read(dir.child("data").path())?, first);
    }
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions compare files generated with short and long aliases."
)]
fn pair_short_and_long_flags_are_equivalent() -> Result<()> {
    let dir = TempDir::new()?;
    for args in [
        vec![
            "-l", "left", "-r", "right", "-a", "1", "-j", "1", "-b", "1", "-c", "2", "-s", "42",
            "-e", "crlf", "-N",
        ],
        vec![
            "--left",
            "left2",
            "--right",
            "right2",
            "--left-only",
            "1",
            "--shared",
            "1",
            "--right-only",
            "1",
            "--copies",
            "2",
            "--seed",
            "42",
            "--line-ending",
            "crlf",
            "--no-final-newline",
        ],
    ] {
        command()
            .current_dir(dir.path())
            .arg("pair")
            .args(args)
            .assert()
            .success()
            .stderr("");
    }
    assert_eq!(
        fs::read(dir.child("left").path())?,
        fs::read(dir.child("left2").path())?
    );
    assert_eq!(
        fs::read(dir.child("right").path())?,
        fs::read(dir.child("right2").path())?
    );
    Ok(())
}

#[test]
fn record_corpus_empty_limits_and_trailing_terminators() -> Result<()> {
    let dir = TempDir::new()?;
    for (input, expected) in [
        (b"".as_slice(), b"".as_slice()),
        (b"\n", b"\n"),
        (b"a\n", b"a\n"),
        (b"a", b"a\n"),
        (b"\n\n", b"\n\n"),
    ] {
        dir.child("input").write_binary(input)?;
        command()
            .current_dir(dir.path())
            .args(["records", "-", "-f", "input"])
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
    for content in [vec![b'\n'; 100_001], vec![b'x'; 8 * 1024 * 1024 + 1]] {
        dir.child("input").write_binary(&content)?;
        command()
            .current_dir(dir.path())
            .args(["records", "out", "-f", "input"])
            .assert()
            .code(1);
        dir.child("out").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn default_text_flag_values_are_never_subcommands() {
    for flag in ["-d", "--word-separator"] {
        for name in ["text", "records", "fields", "bytes", "pair", "help"] {
            command()
                .args([flag, name, "-A", "a", "-n", "1", "-w", "2", "-l", "1", "-"])
                .assert()
                .success()
                .stdout(format!("a{name}a\n"))
                .stderr("");
        }
    }
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify delimiter count in actual bytes."
)]
fn random_plain_fields_exclude_alphanumeric_delimiters() {
    for delimiter in ["A", "z", "0"] {
        let assertion = command()
            .args([
                "fields", "-n", "4", "-f", "3", "-l", "100", "-d", delimiter, "-s", "42", "-",
            ])
            .assert()
            .success()
            .stderr("");
        let bytes = &assertion.get_output().stdout;
        let separator = delimiter.as_bytes().first().copied();
        assert_eq!(
            bytes
                .iter()
                .filter(|byte| Some(**byte) == separator)
                .count(),
            8
        );
        assert_eq!(bytes.len(), 1212);
    }
}

#[cfg(feature = "csv")]
#[test]
fn csv_random_quote_overhead_is_validated_before_truncation() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("keep").write_str("keep")?;
    command()
        .current_dir(dir.path())
        .args([
            "fields", "keep", "-n", "0", "-F", "csv", "-f", "2", "-l", "4194302", "-d", "A", "-s",
            "42",
        ])
        .assert()
        .code(2)
        .stdout("");
    dir.child("keep").assert("keep");
    Ok(())
}

#[cfg(not(feature = "csv"))]
#[test]
fn disabled_csv_is_rejected_before_creating_or_truncating_output() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("keep").write_str("keep")?;
    for path in ["keep", "missing"] {
        for flag in ["-F", "--format"] {
            command()
                .current_dir(dir.path())
                .args(["fields", path, "-n", "0", flag, "csv"])
                .assert()
                .code(2)
                .stdout("")
                .stderr(predicate::str::contains("invalid value 'csv'"));
            dir.child("keep").assert("keep");
            dir.child("missing").assert(predicate::path::missing());
        }
    }
    command()
        .args(["fields", "--help"])
        .assert()
        .success()
        .stdout(predicate::str::contains("[possible values: delimited]"))
        .stderr("");
    Ok(())
}

#[test]
fn invalid_environment_is_ignored_for_help_and_version() {
    for args in [
        vec!["--help"],
        vec!["bytes", "--help"],
        vec!["records", "--version"],
    ] {
        command()
            .args(args)
            .env("CLIS_LOG_LEVEL", "invalid")
            .assert()
            .success()
            .stderr("");
    }
}
