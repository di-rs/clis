use assert_fs::{NamedTempFile, fixture::FileTouch};
use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use std::{fs, os::unix::fs::PermissionsExt};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[test]
fn usage() {
    for flag in &["-h", "--help"] {
        cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .success()
            .stderr("")
            .stdout(predicate::str::contains("Usage"));
    }
}

fn gen_bad_file() -> Result<NamedTempFile> {
    let file = assert_fs::NamedTempFile::new("no-permissions")?;
    file.touch()?;
    fs::set_permissions(file.path(), PermissionsExt::from_mode(0o000))?;
    Ok(file)
}

#[test]
fn skip_bad_file() -> Result<()> {
    let bad = gen_bad_file()?;
    let expected = format!("{}: .* [(]os error 13[)]", bad.path().display());
    cargo_bin_cmd!()
        .arg(bad.path())
        .assert()
        .code(1)
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn rejects_unknown_and_conflicting_flags() {
    for args in [&["--unknown"][..], &["-n", "-b"]] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("error:"));
    }
}

#[test]
fn default_stdin_preserves_empty_and_unterminated_unicode() {
    for input in ["", "é\r\n世界"] {
        cargo_bin_cmd!()
            .write_stdin(input)
            .assert()
            .success()
            .stdout(input)
            .stderr("");
    }
}

#[test]
fn number_nonblank_keeps_whitespace_only_lines() {
    cargo_bin_cmd!()
        .arg("--number-nonblank")
        .write_stdin("\n \n\t\né")
        .assert()
        .success()
        .stdout("\n     1\t \n     2\t\t\n     3\té")
        .stderr("");
}

#[test]
fn squeeze_and_number_preserve_whitespace_lines() {
    cargo_bin_cmd!()
        .args(["-s", "-n"])
        .write_stdin("\n\n \n\n\né")
        .assert()
        .success()
        .stdout("     1\t\n     2\t \n     3\t\n     4\té")
        .stderr("");
}

#[test]
fn missing_operand_returns_failure() {
    cli_testing::cargo_bin_cmd!()
        .args(["/sanctuary-audit-missing/path"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("/sanctuary-audit-missing/path"));
}
