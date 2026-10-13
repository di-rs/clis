use assert_cmd::{Command, cargo::cargo_bin_cmd};
use predicates::prelude::*;

fn assert_silent_status(cmd: &mut Command, status: i32) {
    cmd.assert()
        .stdout(predicate::str::is_empty())
        .stderr(predicate::str::is_empty())
        .code(status);
}

#[test]
fn false_exits_one_silently() {
    assert_silent_status(&mut cargo_bin_cmd!("false"), 1);
}

#[test]
fn false_ignores_arguments() {
    assert_silent_status(cargo_bin_cmd!("false").args(["--help", "ignored"]), 1);
}
