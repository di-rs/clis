use assert_fs::{NamedTempFile, fixture::FileTouch};
use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use std::{fs, os::unix::fs::PermissionsExt};

const EMPTY: &str = "./tests/inputs/empty.txt";
const ONE: &str = "./tests/inputs/one.txt";
const TWO: &str = "./tests/inputs/two.txt";
const THREE: &str = "./tests/inputs/three.txt";
const TWELVE: &str = "./tests/inputs/twelve.txt";

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

fn gen_bad_file() -> Result<NamedTempFile> {
    let file = assert_fs::NamedTempFile::new("no-permissions")?;
    file.touch()?;
    fs::set_permissions(file.path(), PermissionsExt::from_mode(0o000))?;
    Ok(file)
}

#[test]
fn skip_bad_file() -> Result<()> {
    let bad = gen_bad_file()?;
    let bad = bad.path().display().to_string();
    let expected = format!("{bad}: .* [(]os error 13[)]");
    cargo_bin_cmd!()
        .args([EMPTY, &bad])
        .assert()
        .code(1)
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn dies_bad_bytes() {
    let bad = "ddsdsasad";
    let expected = format!(
        "invalid value '{bad}' for \
        '--bytes <BYTES>': invalid digit found in string"
    );
    cargo_bin_cmd!()
        .args(["-c", bad, EMPTY])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_bad_lines() {
    let bad = "ddsdsasad";
    let expected = format!(
        "invalid value '{bad}' for \
        '--lines <LINES>': invalid digit found in string"
    );
    cargo_bin_cmd!()
        .args(["-n", bad, EMPTY])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_bytes_and_lines() {
    let expected = "the argument '--lines <LINES>' cannot be \
               used with '--bytes <BYTES>'";
    cargo_bin_cmd!()
        .args(["-n", "1", "-c", "2"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let contents = cli_testing::fixtures!().read_expected(expected_file)?;
    // headr replaces incomplete UTF-8 sequences; the reference head emits raw bytes.
    let expected = String::from_utf8_lossy(&contents).into_owned();
    cargo_bin_cmd!()
        .args(args)
        .assert()
        .success()
        .stderr("")
        .stdout(expected);
    Ok(())
}

fn run_stdin(input_file: &str, args: &[&str], expected_file: &str) -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fs::read(input_file)?;
    let contents = fixtures.read_expected(expected_file)?;
    // Preserve headr's lossy byte mode while asserting exact resulting bytes.
    let expected = String::from_utf8_lossy(&contents).into_owned();
    cargo_bin_cmd!()
        .args(args)
        .write_stdin(input)
        .assert()
        .success()
        .stderr("")
        .stdout(expected);
    Ok(())
}

#[test]
fn rejects_zero_counts_and_unknown_flag() {
    for args in [["-n", "0"], ["-c", "0"], ["--unknown", "1"]] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("error:"));
    }
}

#[test]
fn explicit_stdin_preserves_line_endings_and_unterminated_line() {
    for (count, expected) in [("1", "é\r\n"), ("2", "é\r\n世界"), ("9", "é\r\n世界")] {
        cargo_bin_cmd!()
            .args(["-n", count, "-"])
            .write_stdin("é\r\n世界")
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn stdin_byte_limit_handles_utf8_boundaries() {
    for (count, expected) in [("1", "�"), ("2", "é"), ("3", "é�"), ("99", "é界")] {
        cargo_bin_cmd!()
            .args(["-c", count])
            .write_stdin("é界")
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn empty_stdin_in_line_and_byte_modes() {
    for mode in ["-n", "-c"] {
        cargo_bin_cmd!()
            .args([mode, "1"])
            .write_stdin("")
            .assert()
            .success()
            .stdout("")
            .stderr("");
    }
}

#[test]
fn empty() -> Result<()> {
    run(&[EMPTY], "empty.txt.out")
}

#[test]
fn empty_n2() -> Result<()> {
    run(&[EMPTY, "-n", "2"], "empty.txt.n2.out")
}

#[test]
fn empty_n4() -> Result<()> {
    run(&[EMPTY, "-n", "4"], "empty.txt.n4.out")
}

#[test]
fn empty_c2() -> Result<()> {
    run(&[EMPTY, "-c", "2"], "empty.txt.c2.out")
}

#[test]
fn empty_c4() -> Result<()> {
    run(&[EMPTY, "-c", "4"], "empty.txt.c4.out")
}

#[test]
fn one() -> Result<()> {
    run(&[ONE], "one.txt.out")
}

#[test]
fn one_n2() -> Result<()> {
    run(&[ONE, "-n", "2"], "one.txt.n2.out")
}

#[test]
fn one_n4() -> Result<()> {
    run(&[ONE, "-n", "4"], "one.txt.n4.out")
}

#[test]
fn one_c1() -> Result<()> {
    run(&[ONE, "-c", "1"], "one.txt.c1.out")
}

#[test]
fn one_c2() -> Result<()> {
    run(&[ONE, "-c", "2"], "one.txt.c2.out")
}

#[test]
fn one_c4() -> Result<()> {
    run(&[ONE, "-c", "4"], "one.txt.c4.out")
}

#[test]
fn one_stdin() -> Result<()> {
    run_stdin(ONE, &[], "one.txt.out")
}

#[test]
fn one_n2_stdin() -> Result<()> {
    run_stdin(ONE, &["-n", "2"], "one.txt.n2.out")
}

#[test]
fn one_n4_stdin() -> Result<()> {
    run_stdin(ONE, &["-n", "4"], "one.txt.n4.out")
}

#[test]
fn one_c1_stdin() -> Result<()> {
    run_stdin(ONE, &["-c", "1"], "one.txt.c1.out")
}

#[test]
fn one_c2_stdin() -> Result<()> {
    run_stdin(ONE, &["-c", "2"], "one.txt.c2.out")
}

#[test]
fn one_c4_stdin() -> Result<()> {
    run_stdin(ONE, &["-c", "4"], "one.txt.c4.out")
}

#[test]
fn two() -> Result<()> {
    run(&[TWO], "two.txt.out")
}

#[test]
fn two_n2() -> Result<()> {
    run(&[TWO, "-n", "2"], "two.txt.n2.out")
}

#[test]
fn two_n4() -> Result<()> {
    run(&[TWO, "-n", "4"], "two.txt.n4.out")
}

#[test]
fn two_c2() -> Result<()> {
    run(&[TWO, "-c", "2"], "two.txt.c2.out")
}

#[test]
fn two_c4() -> Result<()> {
    run(&[TWO, "-c", "4"], "two.txt.c4.out")
}

#[test]
fn two_stdin() -> Result<()> {
    run_stdin(TWO, &[], "two.txt.out")
}

#[test]
fn two_n2_stdin() -> Result<()> {
    run_stdin(TWO, &["-n", "2"], "two.txt.n2.out")
}

#[test]
fn two_n4_stdin() -> Result<()> {
    run_stdin(TWO, &["-n", "4"], "two.txt.n4.out")
}

#[test]
fn two_c2_stdin() -> Result<()> {
    run_stdin(TWO, &["-c", "2"], "two.txt.c2.out")
}

#[test]
fn two_c4_stdin() -> Result<()> {
    run_stdin(TWO, &["-c", "4"], "two.txt.c4.out")
}

// --------------------------------------------------
#[test]
fn three() -> Result<()> {
    run(&[THREE], "three.txt.out")
}

#[test]
fn three_n2() -> Result<()> {
    run(&[THREE, "-n", "2"], "three.txt.n2.out")
}

#[test]
fn three_n4() -> Result<()> {
    run(&[THREE, "-n", "4"], "three.txt.n4.out")
}

#[test]
fn three_c2() -> Result<()> {
    run(&[THREE, "-c", "2"], "three.txt.c2.out")
}

#[test]
fn three_c4() -> Result<()> {
    run(&[THREE, "-c", "4"], "three.txt.c4.out")
}

#[test]
fn three_stdin() -> Result<()> {
    run_stdin(THREE, &[], "three.txt.out")
}

#[test]
fn three_n2_stdin() -> Result<()> {
    run_stdin(THREE, &["-n", "2"], "three.txt.n2.out")
}

#[test]
fn three_n4_stdin() -> Result<()> {
    run_stdin(THREE, &["-n", "4"], "three.txt.n4.out")
}

#[test]
fn three_c2_stdin() -> Result<()> {
    run_stdin(THREE, &["-c", "2"], "three.txt.c2.out")
}

#[test]
fn three_c4_stdin() -> Result<()> {
    run_stdin(THREE, &["-c", "4"], "three.txt.c4.out")
}

#[test]
fn twelve() -> Result<()> {
    run(&[TWELVE], "twelve.txt.out")
}

#[test]
fn twelve_n2() -> Result<()> {
    run(&[TWELVE, "-n", "2"], "twelve.txt.n2.out")
}

#[test]
fn twelve_n4() -> Result<()> {
    run(&[TWELVE, "-n", "4"], "twelve.txt.n4.out")
}

#[test]
fn twelve_c2() -> Result<()> {
    run(&[TWELVE, "-c", "2"], "twelve.txt.c2.out")
}

#[test]
fn twelve_c4() -> Result<()> {
    run(&[TWELVE, "-c", "4"], "twelve.txt.c4.out")
}

#[test]
fn twelve_stdin() -> Result<()> {
    run_stdin(TWELVE, &[], "twelve.txt.out")
}

#[test]
fn twelve_n2_stdin() -> Result<()> {
    run_stdin(TWELVE, &["-n", "2"], "twelve.txt.n2.out")
}

#[test]
fn twelve_n4_stdin() -> Result<()> {
    run_stdin(TWELVE, &["-n", "4"], "twelve.txt.n4.out")
}

#[test]
fn twelve_c2_stdin() -> Result<()> {
    run_stdin(TWELVE, &["-c", "2"], "twelve.txt.c2.out")
}

#[test]
fn twelve_c4_stdin() -> Result<()> {
    run_stdin(TWELVE, &["-c", "4"], "twelve.txt.c4.out")
}

#[test]
fn multiple_files() -> Result<()> {
    run(&[EMPTY, ONE, TWO, THREE, TWELVE], "all.out")
}

#[test]
fn multiple_files_n2() -> Result<()> {
    run(&[EMPTY, ONE, TWO, THREE, TWELVE, "-n", "2"], "all.n2.out")
}

#[test]
fn multiple_files_n4() -> Result<()> {
    run(&["-n", "4", EMPTY, ONE, TWO, THREE, TWELVE], "all.n4.out")
}

#[test]
fn multiple_files_c1() -> Result<()> {
    run(&[EMPTY, ONE, TWO, THREE, TWELVE, "-c", "1"], "all.c1.out")
}

#[test]
fn multiple_files_c2() -> Result<()> {
    run(&[EMPTY, ONE, TWO, THREE, TWELVE, "-c", "2"], "all.c2.out")
}

#[test]
fn multiple_files_c4() -> Result<()> {
    run(&["-c", "4", EMPTY, ONE, TWO, THREE, TWELVE], "all.c4.out")
}

#[test]
fn missing_operand_returns_failure() {
    cli_testing::cargo_bin_cmd!()
        .args(["/sanctuary-audit-missing/path"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("/sanctuary-audit-missing/path"));
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
    let lines = args
        .iter()
        .find(|arg| arg["long"] == "lines")
        .ok_or("missing lines flag")?;
    if lines["short"] != "n"
        || lines["default_values"] != serde_json::json!(["10"])
        || lines["num_args"] != serde_json::json!({"min":1,"max":1})
    {
        return Err("incorrect lines metadata".into());
    }
    cli_testing::cargo_bin_cmd!()
        .env("SANCTUARY_CLI_SCHEMA", "1")
        .args(["-n", "1"])
        .write_stdin("one\ntwo\n")
        .assert()
        .code(0)
        .stdout("one\n")
        .stderr("");
    Ok(())
}
