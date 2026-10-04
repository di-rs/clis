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
            .success()
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
fn dies_bad_file() {
    let bad = gen_bad_file();
    let expected = format!("cannot open file {bad}");
    cargo_bin_cmd!()
        .args([&bad])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

fn run(args: &[&str], expected: &str) -> Result<()> {
    let output = cargo_bin_cmd!().args(args).assert().success().stderr("");

    let stdout = String::from_utf8(output.get_output().stdout.clone())?;
    assert_eq!(stdout.trim_end(), expected);
    Ok(())
}

fn run_stdin(args: &[&str], input_file: &str, expected: &str) -> Result<()> {
    let input = fs::read_to_string(input_file)?;

    let output = cargo_bin_cmd!()
        .write_stdin(input)
        .args(args)
        .assert()
        .success()
        .stderr("");

    let stdout = String::from_utf8(output.get_output().stdout.clone())?;
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

#[test]
fn nested_file_preserves_sibling_and_attribute_order() -> Result<()> {
    let expected = fs::read_to_string("tests/expected/nested.txt")?;
    run(&["tests/inputs/nested.xml"], expected.trim_end())
}

#[test]
fn nested_stdin_preserves_unicode_and_empty_attributes() -> Result<()> {
    let expected = fs::read_to_string("tests/expected/nested.txt")?;
    run_stdin(&["-"], "tests/inputs/nested.xml", expected.trim_end())
}

#[test]
fn self_closing_element_without_attributes() {
    cargo_bin_cmd!()
        .arg("-")
        .write_stdin("<leaf/>")
        .assert()
        .success()
        .stderr("")
        .stdout("Element { name: \"leaf\", attributes: [], children: [] }\n");
}

#[test]
fn empty_parent_with_whitespace_content() {
    cargo_bin_cmd!()
        .arg("-")
        .write_stdin("\t\n<root> \n\t </root>\r\n")
        .assert()
        .success()
        .stderr("")
        .stdout("Element { name: \"root\", attributes: [], children: [] }\n");
}

#[test]
fn dies_empty_or_whitespace_stdin() {
    for input in ["", " \t\r\n"] {
        assert_malformed_stdin(input);
    }
}

fn assert_malformed_stdin(input: &str) {
    cargo_bin_cmd!()
        .arg("-")
        .write_stdin(input)
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("cannot parse part of the input"));
}

const MALFORMED: &[&str] = &[
    "tests/inputs/mismatched.xml",
    "tests/inputs/truncated.xml",
    "tests/inputs/unquoted-attribute.xml",
];

#[test]
fn dies_malformed_files() {
    for file in MALFORMED {
        cargo_bin_cmd!()
            .arg(file)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("cannot parse part of the input"));
    }
}

#[test]
fn dies_malformed_stdin() -> Result<()> {
    for file in MALFORMED {
        assert_malformed_stdin(&fs::read_to_string(file)?);
    }
    Ok(())
}

#[test]
fn dies_trailing_non_xml_input() {
    assert_malformed_stdin("<root/>not XML");
}

#[test]
fn dies_multiple_root_elements() {
    assert_malformed_stdin("<first/>\n<second/>");
}
