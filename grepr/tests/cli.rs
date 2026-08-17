use std::{fs, path::PathBuf};

use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;

const BUSTLE: &str = "tests/inputs/bustle.txt";
const EMPTY: &str = "tests/inputs/empty.txt";
const FOX: &str = "tests/inputs/fox.txt";
const NOBODY: &str = "tests/inputs/nobody.txt";
const INPUTS_DIR: &str = "tests/inputs";

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
        .stderr(predicate::str::contains("Invalid pattern `*foo`"));
}

#[test]
fn warns_bad_file() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("{bad}: .* [(]os error 2[)]");
    cargo_bin_cmd!()
        .args(["foo", &bad])
        .assert()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = fs::read_to_string(outfile)?;
    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}

#[test]
fn empty_file() -> Result<()> {
    run(&["foo", EMPTY], "empty.foo")
}

#[test]
fn empty_regex() -> Result<()> {
    run(&["", FOX], "empty_regex.fox.txt")
}

#[test]
fn bustle_capitalized() -> Result<()> {
    run(&["The", BUSTLE], "bustle.txt.the.capitalized")
}

#[test]
fn bustle_lowercase() -> Result<()> {
    run(&["the", BUSTLE], "bustle.txt.the.lowercase")
}

#[test]
fn bustle_insensitive() -> Result<()> {
    run(
        &["--insensitive", "the", BUSTLE],
        "bustle.txt.the.lowercase.insensitive",
    )
}

#[test]
fn nobody() -> Result<()> {
    run(&["nobody", NOBODY], "nobody.txt")
}

#[test]
fn nobody_insensitive() -> Result<()> {
    run(&["-i", "nobody", NOBODY], "nobody.txt.insensitive")
}

#[test]
fn multiple_files() -> Result<()> {
    run(&["The", BUSTLE, EMPTY, FOX, NOBODY], "all.the.capitalized")
}

#[test]
fn multiple_files_insensitive() -> Result<()> {
    run(
        &["-i", "the", BUSTLE, EMPTY, FOX, NOBODY],
        "all.the.lowercase.insensitive",
    )
}

#[test]
fn recursive() -> Result<()> {
    run(&["--recursive", "dog", INPUTS_DIR], "dog.recursive")
}

#[test]
fn recursive_insensitive() -> Result<()> {
    run(&["-ri", "then", INPUTS_DIR], "the.recursive.insensitive")
}

#[test]
fn sensitive_count_capital() -> Result<()> {
    run(
        &["--count", "The", BUSTLE],
        "bustle.txt.the.capitalized.count",
    )
}

#[test]
fn sensitive_count_lower() -> Result<()> {
    run(
        &["--count", "the", BUSTLE],
        "bustle.txt.the.lowercase.count",
    )
}

#[test]
fn insensitive_count() -> Result<()> {
    run(
        &["-ci", "the", BUSTLE],
        "bustle.txt.the.lowercase.insensitive.count",
    )
}

#[test]
fn nobody_count() -> Result<()> {
    run(&["-c", "nobody", NOBODY], "nobody.txt.count")
}

#[test]
fn nobody_count_insensitive() -> Result<()> {
    run(&["-ci", "nobody", NOBODY], "nobody.txt.insensitive.count")
}

#[test]
fn sensitive_count_multiple() -> Result<()> {
    run(
        &["-c", "The", BUSTLE, EMPTY, FOX, NOBODY],
        "all.the.capitalized.count",
    )
}

#[test]
fn insensitive_count_multiple() -> Result<()> {
    run(
        &["-ic", "the", BUSTLE, EMPTY, FOX, NOBODY],
        "all.the.lowercase.insensitive.count",
    )
}

#[test]
fn warns_dir_not_recursive() {
    let stdout = "tests/inputs/fox.txt:\
        The quick brown fox jumps over the lazy dog.";
    cargo_bin_cmd!()
        .args(["fox", INPUTS_DIR, FOX])
        .assert()
        .stderr(predicate::str::contains("tests/inputs is a directory"))
        .stdout(predicate::str::contains(stdout));
}

#[test]
fn stdin() -> Result<()> {
    let input = fs::read_to_string(BUSTLE)?;
    let expected = fs::read_to_string("tests/expected/bustle.txt.the.capitalized")?;

    let output = cargo_bin_cmd!().arg("The").write_stdin(input).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}

#[test]
fn stdin_insensitive_count() -> Result<()> {
    let files = &[BUSTLE, EMPTY, FOX, NOBODY];

    let mut input = String::new();
    for file in files {
        input += &fs::read_to_string(file)?;
    }

    let expected_file = "tests/expected/the.recursive.insensitive.count.stdin";
    let expected = fs::read_to_string(expected_file)?;

    let output = cargo_bin_cmd!()
        .args(["-ci", "the", "-"])
        .write_stdin(input)
        .output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}
