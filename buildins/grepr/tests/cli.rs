use assert_fs::{NamedTempFile, TempDir, prelude::*};
use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{fs, path::PathBuf};

const FOX: &str = "tests/inputs/fox.txt";
const INPUTS_DIR: &str = "tests/inputs";

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[test]
fn usage() {
    for flag in &["-h", "--help"] {
        cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .success()
            .stdout(predicate::str::contains("Usage"));
    }
}

fn gen_bad_file() -> String {
    "tests/file/doesnt/exist".to_owned()
}

#[test]
fn dies_no_args() {
    cargo_bin_cmd!()
        .assert()
        .failure()
        .stderr(predicate::str::contains("Usage"));
}

#[test]
fn dies_bad_pattern() {
    cargo_bin_cmd!()
        .args(["*foo", FOX])
        .assert()
        .failure()
        .stderr(predicate::str::contains("invalid pattern passed `*foo`"));
}

#[test]
fn warns_bad_file() {
    let bad = gen_bad_file();
    let expected = format!("{bad} doesn\'t exists");
    cargo_bin_cmd!()
        .args(["foo", &bad])
        .assert()
        .code(1)
        .stderr(predicate::str::contains(expected));
}

#[test]
fn warns_dir_not_recursive() {
    let stdout = "tests/inputs/fox.txt:\
        The quick brown fox jumps over the lazy dog.";
    cargo_bin_cmd!()
        .args(["fox", INPUTS_DIR, FOX])
        .assert()
        .code(1)
        .stderr(predicate::str::contains("tests/inputs is a directory"))
        .stdout(predicate::str::contains(stdout));
}

#[test]
fn file_content_in_file() -> Result<()> {
    let file = NamedTempFile::new("sample.txt")?;
    file.write_str("A test\nActual content\nMore content\nAnother test")?;

    let mut cmd = cargo_bin_cmd!();
    cmd.arg("test").arg(file.path());

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("A test\nAnother test"));

    Ok(())
}

#[test]
fn empty_pattern_returns_all_file() -> Result<()> {
    let file = NamedTempFile::new("sample.txt")?;
    let file_content = "A test\nActual content\nMore content\nAnother test";
    file.write_str(file_content)?;

    let mut cmd = cargo_bin_cmd!();
    cmd.arg("").arg(file.path());

    cmd.assert()
        .success()
        .stdout(predicate::str::contains(file_content));

    Ok(())
}

fn check_stdin(args: &[&str], input: &str, expected: &str) {
    cargo_bin_cmd!()
        .args(args)
        .write_stdin(input)
        .assert()
        .success()
        .stdout(expected.to_owned())
        .stderr("");
}

#[test]
fn empty_regex_counts_blank_and_unterminated_lines() {
    check_stdin(&["-c", ""], "alpha\n\nomega", "3\n");
}

#[test]
fn empty_regex_inverted_selects_nothing() {
    check_stdin(&["-v", ""], "alpha\n\nomega", "");
}

#[test]
fn invert_preserves_blank_lines_and_missing_final_newline() {
    check_stdin(
        &["--invert-match", "alpha", "-"],
        "alpha\n\nomega",
        "\nomega",
    );
}

#[test]
fn insensitive_inverted_count() {
    check_stdin(&["-ivc", "alpha"], "ALPHA\nalpha\nbeta\n\n", "2\n");
}

#[test]
fn count_counts_matching_lines_not_occurrences() {
    check_stdin(&["-c", "cat"], "cat cat cat\ndog\ncat", "2\n");
}

#[test]
fn stdin_invalid_regex_produces_no_output() {
    cargo_bin_cmd!()
        .arg("[")
        .write_stdin("alpha\n")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("invalid pattern passed `[`"));
}

#[test]
fn stdin_and_file_are_both_prefixed() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("sample.txt").write_str("skip\nmatch file\n")?;
    cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["match", "-", "sample.txt"])
        .write_stdin("match stdin\nskip\n")
        .assert()
        .success()
        .stdout("-:match stdin\nsample.txt:match file\n")
        .stderr("");
    Ok(())
}

#[test]
fn multiple_files_inverted_count_includes_zero() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("first.txt").write_str("cat\ndog\n\n")?;
    dir.child("second.txt").write_str("CAT\n")?;
    cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["-ivc", "cat", "first.txt", "second.txt"])
        .assert()
        .success()
        .stdout("first.txt:2\nsecond.txt:0\n")
        .stderr("");
    Ok(())
}

#[test]
fn recursive_search_skips_empty_nested_directories() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("tree/nested/empty").create_dir_all()?;
    cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["-rc", "match", "tree"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    Ok(())
}

#[test]
fn recursive_search_preserves_explicit_files_and_missing_path_errors() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("sample.txt").write_str("match file\n")?;
    cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["-r", "match", "missing.txt", "sample.txt"])
        .assert()
        .code(1)
        .stdout("sample.txt:match file\n")
        .stderr("missing.txt doesn't exists\n");
    Ok(())
}

#[test]
fn recursive_search_visits_nested_files_without_reading_directories() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("tree/top.txt").write_str("match top\n")?;
    dir.child("tree/nested/bottom.txt")
        .write_str("match bottom\n")?;
    let cmd = cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["-r", "match", "tree"])
        .assert()
        .success()
        .stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let mut lines: Vec<_> = stdout.lines().collect();
    lines.sort_unstable();
    let expected = [
        format!(
            "{}:match bottom",
            PathBuf::from("tree")
                .join("nested")
                .join("bottom.txt")
                .display()
        ),
        format!(
            "{}:match top",
            PathBuf::from("tree").join("top.txt").display()
        ),
    ];
    assert_eq!(lines, expected);
    Ok(())
}

#[test]
fn debug_logging_preserves_match_output_without_storing_input() -> Result<()> {
    let logs = TempDir::new()?;
    let secret = "private-match-payload-782541";
    cargo_bin_cmd!()
        .env("SANCTUARY_LOG_DIR", logs.path())
        .env("SANCTUARY_LOG_LEVEL", "debug")
        .args(["--verbose", "--verbose", "--verbose", "private-match"])
        .write_stdin(format!("{secret}\n"))
        .assert()
        .success()
        .stdout(format!("{secret}\n"))
        .stderr(predicate::str::contains(secret).not());
    let mut persisted = String::new();
    for entry in fs::read_dir(logs.path())? {
        let path = entry?.path();
        if path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().starts_with("grepr_"))
            && path
                .extension()
                .is_some_and(|extension| extension == "jsonl")
        {
            persisted.push_str(&fs::read_to_string(path)?);
        }
    }
    if !(persisted.contains("matched input line")) {
        return Err("missing match event".into());
    }
    if persisted.contains(secret) {
        return Err("input content leaked into diagnostic file".into());
    }
    Ok(())
}

#[test]
fn missing_operand_returns_failure() {
    cli_testing::cargo_bin_cmd!()
        .args(["x", "/sanctuary-audit-missing/path"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("/sanctuary-audit-missing/path"));
}

#[test]
#[cfg(unix)]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify subprocess interruption; Result propagates setup errors."
)]
fn sigint_terminates_without_stdout_chatter() -> Result<()> {
    use std::{
        os::unix::process::ExitStatusExt,
        process::{Command, Stdio},
        time::{Duration, Instant},
    };
    let mut child = Command::new(cargo_bin_cmd!().get_program())
        .args(["x", "-"])
        .env("SANCTUARY_LOG_LEVEL", "off")
        .env("SANCTUARY_LOG_STDERR", "off")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    // Give startup time to install any signal handler while stdin stays open.
    std::thread::sleep(Duration::from_millis(200));
    let sent = Command::new("kill")
        .args(["-INT", &child.id().to_string()])
        .status();
    let start = Instant::now();
    let mut interrupted = false;
    if sent.is_ok_and(|status| status.success()) {
        while start.elapsed() < Duration::from_secs(2) {
            if child.try_wait()?.is_some() {
                interrupted = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    if !interrupted {
        child.kill()?;
    }
    let output = child.wait_with_output()?;
    assert!(interrupted, "SIGINT did not terminate the blocked search");
    assert_eq!(output.status.signal(), Some(2));
    assert_eq!(output.stdout, b"");
    assert_eq!(output.stderr, b"");
    Ok(())
}
