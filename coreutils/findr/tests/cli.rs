use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::fs;
#[cfg(not(windows))]
use std::{borrow::Cow, path::Path};

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

#[test]
fn dies_bad_name() {
    cargo_bin_cmd!()
        .args(["--name", "*.csv"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("error: invalid value '*.csv'"));
}

#[test]
fn dies_bad_type() {
    let expected = "error: invalid value 'x' for '--type [<TYPE>...]'";
    cargo_bin_cmd!()
        .args(["--type", "x"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[cfg(windows)]
fn format_file_name(expected_file: &str) -> Cow<str> {
    // Equivalent to: Cow::Owned(format!("{}.windows", expected_file))
    format!("{}.windows", expected_file).into()
}

#[cfg(not(windows))]
fn format_file_name(expected_file: &str) -> Cow<'_, str> {
    // Equivalent to: Cow::Borrowed(expected_file)
    expected_file.into()
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let file = format_file_name(expected_file);
    let contents = fs::read_to_string(file.as_ref())?;
    let mut expected: Vec<&str> = contents.split('\n').filter(|s| !s.is_empty()).collect();
    expected.sort_unstable();

    let cmd = cargo_bin_cmd!().args(args).assert().success();
    let out = cmd.get_output();
    let stdout = String::from_utf8(out.stdout.clone())?;
    let mut lines: Vec<&str> = stdout.split('\n').filter(|s| !s.is_empty()).collect();
    lines.sort_unstable();

    assert_eq!(lines, expected);

    Ok(())
}

#[test]
fn path1() -> Result<()> {
    run(&["tests/inputs"], "tests/expected/path1.txt")
}

#[test]
fn path_a() -> Result<()> {
    run(&["tests/inputs/a"], "tests/expected/path_a.txt")
}

#[test]
fn path_a_b() -> Result<()> {
    run(&["tests/inputs/a/b"], "tests/expected/path_a_b.txt")
}

#[test]
fn path_d() -> Result<()> {
    run(&["tests/inputs/d"], "tests/expected/path_d.txt")
}

#[test]
fn path_a_b_d() -> Result<()> {
    run(
        &["tests/inputs/a/b", "tests/inputs/d"],
        "tests/expected/path_a_b_d.txt",
    )
}

#[test]
fn type_f() -> Result<()> {
    run(&["tests/inputs", "-t", "f"], "tests/expected/type_f.txt")
}

#[test]
fn type_f_path_a() -> Result<()> {
    run(
        &["tests/inputs/a", "-t", "f"],
        "tests/expected/type_f_path_a.txt",
    )
}

#[test]
fn type_f_path_a_b() -> Result<()> {
    run(
        &["tests/inputs/a/b", "--type", "f"],
        "tests/expected/type_f_path_a_b.txt",
    )
}

#[test]
fn type_f_path_d() -> Result<()> {
    run(
        &["tests/inputs/d", "--type", "f"],
        "tests/expected/type_f_path_d.txt",
    )
}

#[test]
fn type_f_path_a_b_d() -> Result<()> {
    run(
        &["tests/inputs/a/b", "tests/inputs/d", "--type", "f"],
        "tests/expected/type_f_path_a_b_d.txt",
    )
}

#[test]
fn type_d() -> Result<()> {
    run(&["tests/inputs", "-t", "d"], "tests/expected/type_d.txt")
}

#[test]
fn type_d_path_a() -> Result<()> {
    run(
        &["tests/inputs/a", "-t", "d"],
        "tests/expected/type_d_path_a.txt",
    )
}

#[test]
fn type_d_path_a_b() -> Result<()> {
    run(
        &["tests/inputs/a/b", "--type", "d"],
        "tests/expected/type_d_path_a_b.txt",
    )
}

#[test]
fn type_d_path_d() -> Result<()> {
    run(
        &["tests/inputs/d", "--type", "d"],
        "tests/expected/type_d_path_d.txt",
    )
}

#[test]
fn type_d_path_a_b_d() -> Result<()> {
    run(
        &["tests/inputs/a/b", "tests/inputs/d", "--type", "d"],
        "tests/expected/type_d_path_a_b_d.txt",
    )
}

#[test]
fn type_l() -> Result<()> {
    run(&["tests/inputs", "-t", "l"], "tests/expected/type_l.txt")
}

#[test]
fn type_f_l() -> Result<()> {
    run(
        &["tests/inputs", "-t", "l", "f"],
        "tests/expected/type_f_l.txt",
    )
}

#[test]
fn name_csv() -> Result<()> {
    run(
        &["tests/inputs", "-n", ".*[.]csv"],
        "tests/expected/name_csv.txt",
    )
}

#[test]
fn name_csv_mp3() -> Result<()> {
    run(
        &["tests/inputs", "-n", ".*[.]csv", "-n", ".*[.]mp3"],
        "tests/expected/name_csv_mp3.txt",
    )
}

#[test]
fn name_txt_path_a_d() -> Result<()> {
    run(
        &["tests/inputs/a", "tests/inputs/d", "--name", ".*.txt"],
        "tests/expected/name_txt_path_a_d.txt",
    )
}

#[test]
fn name_a() -> Result<()> {
    run(&["tests/inputs", "-n", "a"], "tests/expected/name_a.txt")
}

#[test]
fn type_f_name_a() -> Result<()> {
    run(
        &["tests/inputs", "-t", "f", "-n", "a"],
        "tests/expected/type_f_name_a.txt",
    )
}

#[test]
fn type_d_name_a() -> Result<()> {
    run(
        &["tests/inputs", "--type", "d", "--name", "a"],
        "tests/expected/type_d_name_a.txt",
    )
}

#[test]
fn path_g() -> Result<()> {
    run(&["tests/inputs/g.csv"], "tests/expected/path_g.txt")
}

#[test]
#[cfg(not(windows))]
#[allow(clippy::panic_in_result_fn)]
fn unreadable_dir() -> Result<()> {
    let dirname = "tests/inputs/cant-touch-this";
    if !Path::new(dirname).exists() {
        fs::create_dir(dirname)?;
    }

    std::process::Command::new("chmod")
        .args(["000", dirname])
        .status()?;

    let cmd = cargo_bin_cmd!().arg("tests/inputs").assert().success();
    fs::remove_dir(dirname)?;

    let out = cmd.get_output();
    let stdout = String::from_utf8(out.stdout.clone())?;

    assert_eq!(stdout.split('\n').filter(|s| !s.is_empty()).count(), 17);

    let stderr = String::from_utf8(out.stderr.clone())?;
    assert!(stderr.contains("cant-touch-this: Permission denied"));
    Ok(())
}
