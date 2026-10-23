use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use std::fs;

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
            .success()
            .stderr("")
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
    let expected = cli_testing::fixtures!().read_expected(expected_file)?;
    cargo_bin_cmd!()
        .args(args)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

fn run_stdin(args: &[&str], input_file: &str, expected_file: &str) -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fs::read(input_file)?;
    let expected = fixtures.read_expected(expected_file)?;
    cargo_bin_cmd!()
        .args(args)
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

#[test]
fn rejects_unknown_flag() {
    cargo_bin_cmd!()
        .args(["--unknown", EMPTY, EMPTY])
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("--unknown"));
}

#[test]
fn stdin_on_either_side_emits_visible_columns() {
    for (args, expected) in [
        (["-", FILE1], "\t\ta\n\tb\nbb\n\tc\n\td\nz\n"),
        ([FILE1, "-"], "\t\ta\nb\n\tbb\nc\nd\n\tz\n"),
    ] {
        cargo_bin_cmd!()
            .args(args)
            .write_stdin("a\nbb\nz")
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn custom_delimiter_handles_blank_common_line() {
    for (delimiter, expected) in [("::", "::::\nα\n"), ("", "\nα\n")] {
        cargo_bin_cmd!()
            .args(["-d", delimiter, "-", BLANK])
            .write_stdin("\nα")
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn insensitive_stdin_matches_common_column() {
    cargo_bin_cmd!()
        .args(["-i", "-12", "-", FILE1])
        .write_stdin("A\nC")
        .assert()
        .success()
        .stdout("a\nc\n")
        .stderr("");
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

#[test]
fn malformed_input_is_not_eof() -> Result<()> {
    let dir = assert_fs::TempDir::new()?;
    let empty = dir.path().join("empty");
    std::fs::write(&empty, b"")?;
    cli_testing::cargo_bin_cmd!()
        .arg("-")
        .arg(empty)
        .write_stdin(b"a\n\xff\nz\n")
        .assert()
        .code(65)
        .stdout("a\n")
        .stderr(predicates::str::contains("Error:"));
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
    let args = value["command"]["args"]
        .as_array()
        .ok_or("missing arguments")?;
    if args
        .iter()
        .filter(|arg| arg["required"] == true && arg["index"].is_number())
        .count()
        != 2
    {
        return Err("required file operands were lost".into());
    }
    Ok(())
}
