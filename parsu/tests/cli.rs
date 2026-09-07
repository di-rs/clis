use assert_cmd::cargo::cargo_bin_cmd;
use color_eyre::Result;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::fs;

const SIMPLE: &str = "tests/inputs/simple.xml";

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
fn dies_bad_file() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("cannot open file {bad}");
    cargo_bin_cmd!()
        .args([&bad])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
    Ok(())
}

fn run(args: &[&str], expected: &str) -> Result<()> {
    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout.trim_end(), expected);
    Ok(())
}

fn run_stdin(args: &[&str], input_file: &str, expected: &str) -> Result<()> {
    let input = fs::read_to_string(input_file)?;

    let output = cargo_bin_cmd!().write_stdin(input).args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout.trim_end(), expected);
    Ok(())
}

#[test]
fn simple() -> Result<()> {
    run(
        &[SIMPLE],
        r#"Element { name: "top", attributes: [("label", "Top")], children: [Element { name: "semi-bottom", attributes: [("label", "Bottom")], children: [] }, Element { name: "middle", attributes: [], children: [Element { name: "bottom", attributes: [("label", "Another bottom")], children: [] }] }] }"#,
    )
}

#[test]
fn stdin_simple() -> Result<()> {
    run_stdin(
        &["-"],
        SIMPLE,
        r#"Element { name: "top", attributes: [("label", "Top")], children: [Element { name: "semi-bottom", attributes: [("label", "Bottom")], children: [] }, Element { name: "middle", attributes: [], children: [Element { name: "bottom", attributes: [("label", "Another bottom")], children: [] }] }] }"#,
    )
}
