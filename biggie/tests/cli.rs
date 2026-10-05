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
fn stdout_contains_only_data_even_with_logging() {
    command()
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
        .stderr(predicate::str::contains("lines_written=2"));
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
        .assert()
        .success()
        .stderr(
            predicate::str::contains("generated output")
                .and(predicate::str::contains("close").not()),
        );
    Ok(())
}

#[test]
fn debug_verbosity_collects_stage_timings() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["-n", "2", "--log-level=debug"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Done, wrote 2 lines"))
        .stderr(
            predicate::str::contains("generate")
                .and(predicate::str::contains("lines_written=2"))
                .and(predicate::str::contains("flush"))
                .and(predicate::str::contains("time.busy="))
                .and(predicate::str::contains("time.idle="))
                .and(predicate::str::contains("\u{1b}").not()),
        );
    assert_text(&dir.path().join("out.txt"), 2)
}

#[test]
fn invalid_environment_level_does_not_create_data() -> Result<()> {
    let dir = TempDir::new()?;
    command()
        .current_dir(dir.path())
        .args(["output", "-n", "1"])
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
            .assert()
            .code(2)
            .stdout("");
        dir.child("out.txt").assert(predicate::path::missing());
    }
    Ok(())
}
