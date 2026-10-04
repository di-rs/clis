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
fn dies_month_0() {
    cargo_bin_cmd!()
        .args(["-m", "0"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "invalid value '0' for '-m <MONTH>': not in the range 1 through 12",
        ));
}

#[test]
fn dies_month_13() {
    cargo_bin_cmd!()
        .args(["-m", "13"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "invalid value '13' for '-m <MONTH>': not in the range 1 through 12",
        ));
}

#[test]
fn dies_invalid_month() {
    cargo_bin_cmd!()
        .args(["-m", "foo"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "invalid value 'foo' for '-m <MONTH>'",
        ));
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
    cargo_bin_cmd!()
        .args(args)
        .assert()
        .success()
        .stdout(expected)
        .stderr("");
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

#[test]
fn usage() {
    for flag in ["-h", "--help"] {
        cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .success()
            .stdout(predicate::str::contains("Usage"));
    }
}

#[test]
fn minimum_year_january() -> Result<()> {
    run(&["1", "-m", "1"], "tests/expected/1-1.txt")
}

#[test]
fn maximum_year_december() -> Result<()> {
    run(&["9999", "-m", "12"], "tests/expected/12-9999.txt")
}

#[test]
fn century_1900_is_not_leap_year() -> Result<()> {
    run(&["1900", "-m", "2"], "tests/expected/2-1900.txt")
}

#[test]
fn century_2000_is_leap_year() -> Result<()> {
    run(&["2000", "-m", "2"], "tests/expected/2-2000.txt")
}

#[test]
fn century_2100_is_not_leap_year() -> Result<()> {
    run(&["2100", "-m", "2"], "tests/expected/2-2100.txt")
}

#[test]
fn mixed_case_month_name() -> Result<()> {
    run(&["2020", "-m", "aPrIl"], "tests/expected/4-2020.txt")
}

#[test]
fn dies_ambiguous_or_empty_month() {
    for month in ["", "m", "j", "a"] {
        cargo_bin_cmd!()
            .args(["2020", "-m", month])
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("invalid value"));
    }
}

#[test]
fn dies_long_year_and_month_in_either_order() {
    for args in [["--year", "-m", "December"], ["-m", "December", "--year"]] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("cannot be used with"));
    }
}

#[test]
fn dies_long_year_and_explicit_year_in_either_order() {
    for args in [["--year", "2000"], ["2000", "--year"]] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("cannot be used with"));
    }
}
