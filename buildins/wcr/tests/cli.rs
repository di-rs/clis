use assert_fs::{NamedTempFile, fixture::FileTouch};
use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use std::{fs, os::unix::fs::PermissionsExt};

const EMPTY: &str = "tests/inputs/empty.txt";
const FOX: &str = "tests/inputs/fox.txt";
const ATLAMAL: &str = "tests/inputs/atlamal.txt";

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
    let expected = format!("{}: .* [(]os error 13[)]", bad.display());
    cargo_bin_cmd!()
        .arg(bad.path())
        .assert()
        .code(1)
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn dies_chars_and_bytes() {
    cargo_bin_cmd!()
        .args(["-m", "-c"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "the argument '--chars' cannot be used with '--bytes'",
        ));
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
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
fn rejects_unknown_flag() {
    cargo_bin_cmd!()
        .arg("--unknown")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("--unknown"));
}

#[test]
fn empty_stdin_has_zero_default_counts() {
    cargo_bin_cmd!()
        .write_stdin("")
        .assert()
        .success()
        .stdout("       0       0       0\n")
        .stderr("");
}

#[test]
fn unicode_whitespace_separates_words_and_bytes_differ_from_chars() {
    for (flag, expected) in [
        ("-c", "      12\n"),
        ("-m", "       6\n"),
        ("-w", "       3\n"),
        ("-lwm", "       1       3       6\n"),
    ] {
        cargo_bin_cmd!()
            .args([flag, "-"])
            .write_stdin("é\u{2003}猫\tβ\n")
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn unterminated_stdin_counts_final_record() {
    cargo_bin_cmd!()
        .write_stdin("é x")
        .assert()
        .success()
        .stdout("       1       2       4\n")
        .stderr("");
}

#[test]
fn mixed_stdin_and_file_totals_use_selected_counts() {
    cargo_bin_cmd!()
        .args(["-wm", "-", EMPTY])
        .write_stdin("é x")
        .assert()
        .success()
        .stdout(format!(
            "       2       3\n       0       0 {EMPTY}\n       2       3 total\n"
        ))
        .stderr("");
}

#[test]
fn empty() -> Result<()> {
    run(&[EMPTY], "empty.txt.out")
}

#[test]
fn fox() -> Result<()> {
    run(&[FOX], "fox.txt.out")
}

#[test]
fn fox_bytes() -> Result<()> {
    run(&["--bytes", FOX], "fox.txt.c.out")
}

#[test]
fn fox_chars() -> Result<()> {
    run(&["--chars", FOX], "fox.txt.m.out")
}

#[test]
fn fox_words() -> Result<()> {
    run(&["--words", FOX], "fox.txt.w.out")
}

#[test]
fn fox_lines() -> Result<()> {
    run(&["--lines", FOX], "fox.txt.l.out")
}

#[test]
fn fox_words_bytes() -> Result<()> {
    run(&["-w", "-c", FOX], "fox.txt.wc.out")
}

#[test]
fn fox_words_lines() -> Result<()> {
    run(&["-w", "-l", FOX], "fox.txt.wl.out")
}

#[test]
fn fox_bytes_lines() -> Result<()> {
    run(&["-l", "-c", FOX], "fox.txt.cl.out")
}

#[test]
fn atlamal() -> Result<()> {
    run(&[ATLAMAL], "atlamal.txt.out")
}

#[test]
fn atlamal_bytes() -> Result<()> {
    run(&["-c", ATLAMAL], "atlamal.txt.c.out")
}

#[test]
fn atlamal_words() -> Result<()> {
    run(&["-w", ATLAMAL], "atlamal.txt.w.out")
}

#[test]
fn atlamal_lines() -> Result<()> {
    run(&["-l", ATLAMAL], "atlamal.txt.l.out")
}

#[test]
fn atlamal_words_bytes() -> Result<()> {
    run(&["-w", "-c", ATLAMAL], "atlamal.txt.wc.out")
}

#[test]
fn atlamal_words_lines() -> Result<()> {
    run(&["-w", "-l", ATLAMAL], "atlamal.txt.wl.out")
}

#[test]
fn atlamal_bytes_lines() -> Result<()> {
    run(&["-l", "-c", ATLAMAL], "atlamal.txt.cl.out")
}

#[test]
fn test_all() -> Result<()> {
    run(&[EMPTY, FOX, ATLAMAL], "all.out")
}

#[test]
fn test_all_lines() -> Result<()> {
    run(&["-l", EMPTY, FOX, ATLAMAL], "all.l.out")
}

#[test]
fn test_all_words() -> Result<()> {
    run(&["-w", EMPTY, FOX, ATLAMAL], "all.w.out")
}

#[test]
fn test_all_bytes() -> Result<()> {
    run(&["-c", EMPTY, FOX, ATLAMAL], "all.c.out")
}

#[test]
fn test_all_words_bytes() -> Result<()> {
    run(&["-cw", EMPTY, FOX, ATLAMAL], "all.wc.out")
}

#[test]
fn test_all_words_lines() -> Result<()> {
    run(&["-wl", EMPTY, FOX, ATLAMAL], "all.wl.out")
}

#[test]
fn test_all_bytes_lines() -> Result<()> {
    run(&["-cl", EMPTY, FOX, ATLAMAL], "all.cl.out")
}

#[test]
fn atlamal_stdin() -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fixtures.read_input("atlamal.txt")?;
    let expected = fixtures.read_expected("atlamal.txt.stdin.out")?;
    cargo_bin_cmd!()
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
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
