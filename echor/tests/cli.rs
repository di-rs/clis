use std::path::PathBuf;

use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;

#[test]
fn dies_no_args() {
    let mut cmd = cargo_bin_cmd!();
    cmd.assert()
        .failure()
        .stderr(predicate::str::contains("Usage"));
}

#[test]
fn runs() {
    let mut cmd = cargo_bin_cmd!();
    cmd.arg("Hello").assert().success();
}

fn run(args: &[&str], expected_file: &str) -> Result<(), std::io::Error> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = std::fs::read_to_string(outfile)?;
    let mut cmd = cargo_bin_cmd!();
    cmd.args(args).assert().success().stdout(expected);
    Ok(())
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
    run(&["-n", "Hello there"], "hello2.n.txt")
}