use assert_cmd::cargo::cargo_bin_cmd;
use assert_fs::{NamedTempFile, fixture::FileTouch};
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{fs, os::unix::fs::PermissionsExt, path::PathBuf};

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
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = fs::read_to_string(&outfile)?;

    let cmd = cargo_bin_cmd!().args(args).assert().success();
    let output = cmd.get_output();
    let stdout = String::from_utf8(output.stdout.clone())?;
    assert_eq!(stdout, expected);

    Ok(())
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
    let input = fs::read_to_string(ATLAMAL)?;
    let expected = fs::read_to_string("tests/expected/atlamal.txt.stdin.out")?;

    let cmd = cargo_bin_cmd!().write_stdin(input).assert().success();
    let output = cmd.get_output();

    let stdout = String::from_utf8(output.stdout.clone())?;
    assert_eq!(stdout, expected);
    Ok(())
}
