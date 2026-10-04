use std::path::PathBuf;

use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;

#[test]
fn dies_no_args() {
    let mut cmd = cargo_bin_cmd!();
    cmd.assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("Usage"));
}

#[test]
fn runs() {
    let mut cmd = cargo_bin_cmd!();
    cmd.arg("Hello")
        .assert()
        .success()
        .stdout("Hello\n")
        .stderr("");
}

fn run(args: &[&str], expected_file: &str) -> Result<(), std::io::Error> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = std::fs::read_to_string(outfile)?;
    let mut cmd = cargo_bin_cmd!();
    cmd.args(args)
        .assert()
        .success()
        .stdout(expected)
        .stderr("");
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
fn rejects_unknown_flag() {
    cargo_bin_cmd!()
        .arg("--unknown")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("--unknown"));
}

#[test]
fn empty_arguments_keep_separators() {
    for (args, expected) in [
        (vec![""], "\n"),
        (vec!["-n", ""], ""),
        (vec!["", "é", ""], " é \n"),
    ] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn end_of_options_preserves_flag_like_text() {
    cargo_bin_cmd!()
        .args(["--", "-n", "--help"])
        .assert()
        .success()
        .stdout("-n --help\n")
        .stderr("");
}

#[test]
fn unicode_and_backslashes_are_literal() {
    cargo_bin_cmd!()
        .args(["-n", "世界", r"\n\t"])
        .assert()
        .success()
        .stdout("世界 \\n\\t")
        .stderr("");
}

#[test]
fn hello1() -> Result<(), std::io::Error> {
    run(&["Hello there"], "hello1.txt")
}

#[test]
fn hello2() -> Result<(), std::io::Error> {
    run(&["Hello", "there"], "hello2.txt")
}

#[test]
fn hello1n() -> Result<(), std::io::Error> {
    run(&["-n", "Hello there"], "hello1.n.txt")
}

#[test]
fn hello2n() -> Result<(), std::io::Error> {
    run(&["-n", "Hello", "there"], "hello2.n.txt")
}
