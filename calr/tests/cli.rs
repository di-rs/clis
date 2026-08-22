use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::fs;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[test]
fn dies_year_0() {
    cargo_bin_cmd!()
        .arg("0")
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "error: invalid value '0' for '[YEAR]': 0 is not in 1..=9999",
        ));
}

#[test]
fn dies_year_10000() {
    cargo_bin_cmd!()
        .arg("10000")
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "error: invalid value \'10000\' \
                for \'[YEAR]\': 10000 is not in 1..=9999",
        ));
}

#[test]
fn dies_invalid_year() {
    cargo_bin_cmd!()
        .arg("foo")
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "error: invalid value \'foo\' for \'[YEAR]\': \
                invalid digit found in string",
        ));
}

#[test]
fn dies_month_0() -> Result<()> {
    let output = cargo_bin_cmd!().args(["-m", "0"]).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, "");

    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr.trim(), "month `0` not in the range 1 through 12");

    Ok(())
}

#[test]
fn dies_month_13() -> Result<()> {
    let output = cargo_bin_cmd!().args(["-m", "13"]).output()?;

    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr.trim(), "month `13` not in the range 1 through 12");
    Ok(())
}

#[test]
fn dies_invalid_month() -> Result<()> {
    let output = cargo_bin_cmd!().args(["-m", "foo"]).output()?;

    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr.trim(), "Invalid month `foo`");
    Ok(())
}

#[test]
fn dies_y_and_month() {
    let expected = "the argument '-m <MONTH>' cannot be used with '--year'";
    cargo_bin_cmd!()
        .args(["-m", "1", "-y"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_y_and_year() {
    let expected = "the argument '--year' cannot be used with '[YEAR]'";
    cargo_bin_cmd!()
        .args(["-y", "2000"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn month_num() {
    let expected = &[
        ("1", "January"),
        ("2", "February"),
        ("3", "March"),
        ("4", "April"),
        ("5", "May"),
        ("6", "June"),
        ("7", "July"),
        ("8", "August"),
        ("9", "September"),
        ("10", "October"),
        ("11", "November"),
        ("12", "December"),
    ];

    for (num, month) in expected {
        cargo_bin_cmd!()
            .args(["-m", num])
            .assert()
            .success()
            .stdout(predicates::str::contains(month.to_string()));
    }
}

#[test]
fn partial_month() {
    let expected = &[
        ("ja", "January"),
        ("f", "February"),
        ("mar", "March"),
        ("ap", "April"),
        ("may", "May"),
        ("jun", "June"),
        ("jul", "July"),
        ("au", "August"),
        ("s", "September"),
        ("n", "November"),
        ("d", "December"),
    ];

    for (arg, month) in expected {
        cargo_bin_cmd!()
            .args(["-m", arg])
            .assert()
            .success()
            .stdout(predicates::str::contains(month.to_string()));
    }
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let expected = fs::read_to_string(expected_file)?;
    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}

#[test]
#[allow(clippy::indexing_slicing)]
fn default_one_month() -> Result<()> {
    let cmd = cargo_bin_cmd!().assert().success();
    let out = cmd.get_output();
    let stdout = String::from_utf8(out.stdout.clone())?;
    let lines: Vec<_> = stdout.split('\n').collect();
    assert_eq!(lines.len(), 9);
    assert_eq!(lines[0].len(), 22);
    Ok(())
}

#[test]
fn test_2_2020_leap_year() -> Result<()> {
    run(&["-m", "2", "2020"], "tests/expected/2-2020.txt")
}

#[test]
fn test_4_2020() -> Result<()> {
    run(&["-m", "4", "2020"], "tests/expected/4-2020.txt")
}

#[test]
fn test_april_2020() -> Result<()> {
    run(&["2020", "-m", "april"], "tests/expected/4-2020.txt")
}

#[test]
fn test_2020() -> Result<()> {
    run(&["2020"], "tests/expected/2020.txt")
}

#[test]
fn year() -> Result<()> {
    let cmd = cargo_bin_cmd!().arg("-y").assert().success();
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let lines = stdout.split('\n');
    assert_eq!(lines.count(), 37);
    Ok(())
}
