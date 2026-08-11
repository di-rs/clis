use std::{fs, os::unix::fs::PermissionsExt, path::PathBuf};

use assert_cmd::cargo::cargo_bin_cmd;
use assert_fs::{NamedTempFile, fixture::FileTouch};
use predicates::prelude::*;

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
        .success()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = std::fs::read_to_string(outfile)?;
    let mut cmd = cargo_bin_cmd!();
    cmd.args(args).assert().success().stdout(expected);
    Ok(())
}

fn run_stdin(input_file: &str, args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = std::fs::read_to_string(outfile)?;
    let input = std::fs::read_to_string(input_file)?;
    let mut cmd = cargo_bin_cmd!();
    cmd.write_stdin(input)
        .args(args)
        .assert()
        .success()
        .stdout(expected);
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
    run(&["-n",SPIDERS], "spiders.txt.n.out")
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
    run(&[SPIDERS_MULTISPACE, "-s", "-n"], "spiders-multispaces.txt.s.n.out")
}

#[test]
fn mutipspaces_s_b() -> Result<()> {
    run(&[SPIDERS_MULTISPACE, "-s", "-b"], "spiders-multispaces.txt.s.b.out")
}

#[test]
fn bustle_u() -> Result<()> {
    run(&[BUSTLE, "-u"], "the-bustle.txt.out")
}