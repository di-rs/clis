use assert_fs::{NamedTempFile, fixture::FileTouch};
use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use std::{fs, os::unix::fs::PermissionsExt};

const EMPTY: &str = "tests/inputs/empty.txt";
const FOX: &str = "tests/inputs/fox.txt";
const SPIDERS: &str = "tests/inputs/spiders.txt";
const BUSTLE: &str = "tests/inputs/the-bustle.txt";
const SPIDERS_MULTISPACE: &str = "tests/inputs/spiders-multispaces.txt";

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
    let expected = format!("{}: .* [(]os error 13[)]", bad.path().display());
    cargo_bin_cmd!()
        .arg(bad.path())
        .assert()
        .code(1)
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
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

fn run_stdin(input_file: &str, args: &[&str], expected_file: &str) -> Result<()> {
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
fn bustle_stdin() -> Result<()> {
    run_stdin(BUSTLE, &["-"], "the-bustle.txt.stdin.out")
}

#[test]
fn bustle_stdin_n() -> Result<()> {
    run_stdin(BUSTLE, &["-n", "-"], "the-bustle.txt.n.stdin.out")
}

#[test]
fn bustle_stdin_b() -> Result<()> {
    run_stdin(BUSTLE, &["-b", "-"], "the-bustle.txt.b.stdin.out")
}

#[test]
fn empty() -> Result<()> {
    run(&[EMPTY], "empty.txt.out")
}

#[test]
fn empty_n() -> Result<()> {
    run(&["-n", EMPTY], "empty.txt.n.out")
}

#[test]
fn empty_b() -> Result<()> {
    run(&["-b", EMPTY], "empty.txt.b.out")
}

#[test]
fn fox() -> Result<()> {
    run(&[FOX], "fox.txt.out")
}

#[test]
fn fox_n() -> Result<()> {
    run(&["-n", FOX], "fox.txt.n.out")
}

#[test]
fn fox_b() -> Result<()> {
    run(&["-b", FOX], "fox.txt.b.out")
}

#[test]
fn spiders() -> Result<()> {
    run(&[SPIDERS], "spiders.txt.out")
}

#[test]
fn spiders_n() -> Result<()> {
    run(&["-n", SPIDERS], "spiders.txt.n.out")
}

#[test]
fn spiders_b() -> Result<()> {
    run(&["-b", SPIDERS], "spiders.txt.b.out")
}

#[test]
fn bustle() -> Result<()> {
    run(&[BUSTLE], "the-bustle.txt.out")
}

#[test]
fn bustle_n() -> Result<()> {
    run(&["-n", BUSTLE], "the-bustle.txt.n.out")
}

#[test]
fn bustle_b() -> Result<()> {
    run(&["-b", BUSTLE], "the-bustle.txt.b.out")
}

#[test]
fn all() -> Result<()> {
    run(&[FOX, SPIDERS, BUSTLE], "all.out")
}

#[test]
fn all_n() -> Result<()> {
    run(&[FOX, SPIDERS, BUSTLE, "-n"], "all.n.out")
}

#[test]
fn all_b() -> Result<()> {
    run(&[FOX, SPIDERS, BUSTLE, "-b"], "all.b.out")
}

#[test]
fn mutipspaces() -> Result<()> {
    run(&[SPIDERS_MULTISPACE], "spiders-multispaces.txt.out")
}

#[test]
fn mutipspaces_s() -> Result<()> {
    run(&[SPIDERS_MULTISPACE, "-s"], "spiders-multispaces.txt.s.out")
}

#[test]
fn mutipspaces_s_n() -> Result<()> {
    run(
        &[SPIDERS_MULTISPACE, "-s", "-n"],
        "spiders-multispaces.txt.s.n.out",
    )
}

#[test]
fn mutipspaces_s_b() -> Result<()> {
    run(
        &[SPIDERS_MULTISPACE, "-s", "-b"],
        "spiders-multispaces.txt.s.b.out",
    )
}

#[test]
fn bustle_u() -> Result<()> {
    run(&[BUSTLE, "-u"], "the-bustle.txt.out")
}

#[test]
fn rejects_unknown_and_conflicting_flags() {
    for args in [&["--unknown"][..], &["-n", "-b"]] {
        cargo_bin_cmd!()
            .args(args)
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("error:"));
    }
}

#[test]
fn default_stdin_preserves_empty_and_unterminated_unicode() {
    for input in ["", "é\r\n世界"] {
        cargo_bin_cmd!()
            .write_stdin(input)
            .assert()
            .success()
            .stdout(input)
            .stderr("");
    }
}

#[test]
fn number_nonblank_keeps_whitespace_only_lines() {
    cargo_bin_cmd!()
        .arg("--number-nonblank")
        .write_stdin("\n \n\t\né")
        .assert()
        .success()
        .stdout("\n     1\t \n     2\t\t\n     3\té")
        .stderr("");
}

#[test]
fn squeeze_and_number_preserve_whitespace_lines() {
    cargo_bin_cmd!()
        .args(["-s", "-n"])
        .write_stdin("\n\n \n\n\né")
        .assert()
        .success()
        .stdout("     1\t\n     2\t \n     3\t\n     4\té")
        .stderr("");
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
    Ok(())
}
