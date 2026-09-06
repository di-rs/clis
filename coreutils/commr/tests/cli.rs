use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{fs, path::PathBuf};

const EMPTY: &str = "tests/inputs/empty.txt";
const FILE1: &str = "tests/inputs/file1.txt";
const FILE2: &str = "tests/inputs/file2.txt";
const BLANK: &str = "tests/inputs/blank.txt";

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
fn dies_bad_file1() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("{bad}: .* [(]os error 2[)]");
    cargo_bin_cmd!()
        .args([&bad, FILE1])
        .assert()
        .failure()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn dies_bad_file2() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("{bad}: .* [(]os error 2[)]");
    cargo_bin_cmd!()
        .args([FILE1, &bad])
        .assert()
        .failure()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn dies_both_stdin() {
    let expected = "Error: both input files cannot be STDIN (`-`)";
    cargo_bin_cmd!()
        .args(["-", "-"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = fs::read_to_string(outfile)?;
    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}

fn run_stdin(args: &[&str], input_file: &str, expected_file: &str) -> Result<()> {
    let input = fs::read_to_string(input_file)?;
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = fs::read_to_string(outfile)?;

    let output = cargo_bin_cmd!().write_stdin(input).args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}

#[test]
fn empty_empty() -> Result<()> {
    run(&[EMPTY, EMPTY], "empty_empty.out")
}

#[test]
fn file1_file1() -> Result<()> {
    run(&[FILE1, FILE1], "file1_file1.out")
}

#[test]
fn file1_file2() -> Result<()> {
    run(&[FILE1, FILE2], "file1_file2.out")
}

#[test]
fn file1_empty() -> Result<()> {
    run(&[FILE1, EMPTY], "file1_empty.out")
}

#[test]
fn empty_file2() -> Result<()> {
    run(&[EMPTY, FILE2], "empty_file2.out")
}

#[test]
fn file1_file2_1() -> Result<()> {
    run(&["-1", FILE1, FILE2], "file1_file2.1.out")
}

#[test]
fn file1_file2_2() -> Result<()> {
    run(&["-2", FILE1, FILE2], "file1_file2.2.out")
}

#[test]
fn file1_file2_3() -> Result<()> {
    run(&["-3", FILE1, FILE2], "file1_file2.3.out")
}

#[test]
fn file1_file2_1_2() -> Result<()> {
    run(&["-12", FILE1, FILE2], "file1_file2.12.out")
}

#[test]
fn file1_file2_2_3() -> Result<()> {
    run(&["-23", FILE1, FILE2], "file1_file2.23.out")
}

#[test]
fn file1_file2_13() -> Result<()> {
    run(&["-13", FILE1, FILE2], "file1_file2.13.out")
}

#[test]
fn file1_file2_123() -> Result<()> {
    run(&["-123", FILE1, FILE2], "file1_file2.123.out")
}

// insensitive

#[test]
fn file1_file2_1_i() -> Result<()> {
    run(&["-1", "-i", FILE1, FILE2], "file1_file2.1.i.out")
}

#[test]
fn file1_file2_2_i() -> Result<()> {
    run(&["-2", "-i", FILE1, FILE2], "file1_file2.2.i.out")
}

#[test]
fn file1_file2_3_i() -> Result<()> {
    run(&["-3", "-i", FILE1, FILE2], "file1_file2.3.i.out")
}

#[test]
fn file1_file2_1_2_i() -> Result<()> {
    run(&["-12", "-i", FILE1, FILE2], "file1_file2.12.i.out")
}

#[test]
fn file1_file2_2_3_i() -> Result<()> {
    run(&["-23", "-i", FILE1, FILE2], "file1_file2.23.i.out")
}

#[test]
fn file1_file2_13_i() -> Result<()> {
    run(&["-13", "-i", FILE1, FILE2], "file1_file2.13.i.out")
}

#[test]
fn file1_file2_123_i() -> Result<()> {
    run(&["-123", "-i", FILE1, FILE2], "file1_file2.123.i.out")
}

#[test]
fn stdin_file1() -> Result<()> {
    run_stdin(&["-123", "-i", "-", FILE2], FILE1, "file1_file2.123.i.out")
}

#[test]
fn stdin_file2() -> Result<()> {
    run_stdin(&["-123", "-i", FILE1, "-"], FILE2, "file1_file2.123.i.out")
}

#[test]
fn file1_file2_delim() -> Result<()> {
    run(&[FILE1, FILE2, "-d", ":"], "file1_file2.delim.out")
}

#[test]
fn file1_file2_1_delim() -> Result<()> {
    run(&[FILE1, FILE2, "-1", "-d", ":"], "file1_file2.1.delim.out")
}

#[test]
fn file1_file2_2_delim() -> Result<()> {
    run(&[FILE1, FILE2, "-2", "-d", ":"], "file1_file2.2.delim.out")
}

#[test]
fn file1_file2_3_delim() -> Result<()> {
    run(&[FILE1, FILE2, "-3", "-d", ":"], "file1_file2.3.delim.out")
}

#[test]
fn file1_file2_12_delim() -> Result<()> {
    run(
        &[FILE1, FILE2, "-12", "-d", ":"],
        "file1_file2.12.delim.out",
    )
}

#[test]
fn file1_file2_23_delim() -> Result<()> {
    run(
        &[FILE1, FILE2, "-23", "-d", ":"],
        "file1_file2.23.delim.out",
    )
}

#[test]
fn file1_file2_13_delim() -> Result<()> {
    run(
        &[FILE1, FILE2, "-13", "-d", ":"],
        "file1_file2.13.delim.out",
    )
}

#[test]
fn file1_file2_123_delim() -> Result<()> {
    run(
        &[FILE1, FILE2, "-123", "-d", ":"],
        "file1_file2.123.delim.out",
    )
}

#[test]
fn blank_file1() -> Result<()> {
    run(&[BLANK, FILE1], "blank_file1.out")
}
