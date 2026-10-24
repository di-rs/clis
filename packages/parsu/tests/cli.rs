use cli_testing::cargo_bin_cmd;
use color_eyre::Result;
use predicates::prelude::*;
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

fn run(args: &[&str], expected: &str) {
    cargo_bin_cmd!()
        .args(args)
        .assert()
        .success()
        .stderr("")
        .stdout(format!("{expected}\n"));
}

fn run_stdin(args: &[&str], input_file: &str, expected: &str) -> Result<()> {
    let input = fs::read(input_file)?;
    cargo_bin_cmd!()
        .write_stdin(input)
        .args(args)
        .assert()
        .success()
        .stderr("")
        .stdout(format!("{expected}\n"));
    Ok(())
}

#[test]
fn simple() {
    run(
        &[SIMPLE],
        r#"Element { name: "top", attributes: [("label", "Top")], children: [Element { name: "semi-bottom", attributes: [("label", "Bottom")], children: [] }, Element { name: "middle", attributes: [], children: [Element { name: "bottom", attributes: [("label", "Another bottom")], children: [] }] }] }"#,
    );
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
    let expected = cli_testing::fixtures!().read_expected("nested.txt")?;
    cargo_bin_cmd!()
        .arg("tests/inputs/nested.xml")
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

#[test]
fn nested_stdin_preserves_unicode_and_empty_attributes() -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fixtures.read_input("nested.xml")?;
    let expected = fixtures.read_expected("nested.txt")?;
    cargo_bin_cmd!()
        .arg("-")
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
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

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify observable CLI behavior; Result propagates fixture errors."
)]
fn json_tree_and_errors_are_machine_readable() -> Result<()> {
    let result = cargo_bin_cmd!()
        .args(["--json", "-"])
        .write_stdin("<root a=\"b\"><child/></root>")
        .assert()
        .code(0)
        .stderr("");
    let value: serde_json::Value = serde_json::from_slice(&result.get_output().stdout)?;
    assert_eq!(
        value,
        serde_json::json!({"ok": true, "data": {"name": "root", "attributes": [["a","b"]], "children": [{"name":"child", "attributes":[],"children":[]}]}})
    );
    for (args, status) in [(vec!["--json", "-"], 1), (vec!["--json", "--unknown"], 2)] {
        let result = cargo_bin_cmd!()
            .args(args)
            .write_stdin("not-xml-secret")
            .assert()
            .code(status)
            .stderr("");
        let value: serde_json::Value = serde_json::from_slice(&result.get_output().stdout)?;
        assert_eq!(value["ok"], false);
        assert!(!String::from_utf8_lossy(&result.get_output().stdout).contains("not-xml-secret"));
    }
    Ok(())
}

#[test]
fn exports_public_cli_schema_without_application_side_effects()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    let logs = assert_fs::TempDir::new()?;
    let output = cli_testing::cargo_bin_cmd!()
        .env("SANCTUARY_CLI_SCHEMA", "1")
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_DIR", logs.path())
        .arg("--help")
        .assert()
        .code(0)
        .stderr("")
        .get_output()
        .stdout
        .clone();
    let value: serde_json::Value = serde_json::from_slice(&output)?;
    if value["schema_version"] != 1 {
        return Err("unexpected schema version".into());
    }
    if value["command"]["name"] != env!("CARGO_PKG_NAME") {
        return Err("unexpected CLI identity".into());
    }
    if !value["command"]["args"].is_array() {
        return Err("missing argument metadata".into());
    }
    if std::fs::read_dir(logs.path())?.count() != 0 {
        return Err("schema export wrote logs".into());
    }
    Ok(())
}
