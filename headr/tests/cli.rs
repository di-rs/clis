use assert_cmd::cargo::cargo_bin_cmd;
use assert_fs::{NamedTempFile, fixture::FileTouch};
use predicates::prelude::*;
use std::{fs, os::unix::fs::PermissionsExt, path::PathBuf};

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
        .success()
        .stderr(predicate::str::contains(expected));
    Ok(())
}

#[test]
fn dies_bad_bytes() -> Result<()> {
    let bad = gen_bad_file()?;
    let bad = bad.path().display().to_string();
    let expected = format!(
        "invalid value '{bad}' for \
        '--bytes <BYTES>': invalid digit found in string"
    );
    cargo_bin_cmd!()
        .args(["-c", &bad, EMPTY])
        .assert()
        .success()
        .stderr(predicate::str::contains(expected));
    Ok(())
}

#[test]
fn dies_bad_lines() -> Result<()> {
    let bad = gen_bad_file()?;
    let bad = bad.path().display().to_string();
    let expected = format!(
        "invalid value '{bad}' for \
        '--lines <LINES>': invalid digit found in string"
    );
    cargo_bin_cmd!()
        .args(["-n", &bad, EMPTY])
        .assert()
        .success()
        .stderr(predicate::str::contains(expected));
    Ok(())
}

#[test]
fn dies_bytes_and_lines() {
    let expected = "the argument '--lines <LINES>' cannot be \
               used with '--bytes <BYTES>'";
    cargo_bin_cmd!()
        .args(["-n", "1", "-c", "2"])
        .assert()
        .success()
        .stderr(predicate::str::contains(expected));
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
