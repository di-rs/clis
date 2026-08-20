use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{fs, path::PathBuf};

const FORTUNE_DIR: &str = "./tests/inputs";
const EMPTY_DIR: &str = "./tests/inputs/empty";
const JOKES: &str = "./tests/inputs/jokes";
const LITERATURE: &str = "./tests/inputs/literature";
const QUOTES: &str = "./tests/inputs/quotes";

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

fn gen_bad_file() -> String {
    "tests/file/doesnt/exist".to_owned()
}

#[test]
fn dies_not_enough_args() -> Result<()> {
    let expected = "the following required arguments were not provided:\n  \
        <FILE>...";
    cargo_bin_cmd!()
        .assert()
        .failure()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn dies_bad_file() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("{bad}: .* [(]os error 2[)]");
    cargo_bin_cmd!()
        .args([LITERATURE, &bad])
        .assert()
        .failure()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

#[test]
fn dies_bad_pattern() {
    let expected = r#"Invalid --pattern "*""#;
    cargo_bin_cmd!()
        .args(["--pattern", "*", LITERATURE])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_bad_seed() {
    let bad = gen_bad_file();
    let expected = format!("invalid value '{bad}' for '--seed <SEED>'");
    cargo_bin_cmd!()
        .args([LITERATURE, "--seed", &bad])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = fs::read_to_string(&outfile)?;
    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(stdout, expected);
    Ok(())
}

fn run_outfiles(args: &[&str], out_file: &str, err_file: &str) -> Result<()> {
    let expected_out = fs::read_to_string(out_file)?;
    let expected_err = fs::read_to_string(err_file)?;

    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8(output.clone().stdout)?;
    assert_eq!(stdout, expected_out);

    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr, expected_err);
    Ok(())
}

#[test]
fn no_fortunes_found() -> Result<()> {
    run(&[EMPTY_DIR], "No fortunes found\n")
}

#[test]
fn quotes_seed_1() -> Result<()> {
    run(
        &[QUOTES, "-s", "1"],
        "You can observe a lot just by watching.\n-- Yogi Berra\n",
    )
}

#[test]
fn jokes_seed_1() -> Result<()> {
    run(
        &[JOKES, "-s", "1"],
        "Q: What happens when frogs park illegally?\nA: They get toad.\n",
    )
}

#[test]
fn dir_seed_10() -> Result<()> {
    run(
        &[FORTUNE_DIR, "-s", "10"],
        "Q: Why did the fungus and the alga marry?\n\
        A: Because they took a lichen to each other!\n",
    )
}

#[test]
fn yogi_berra_cap() -> Result<()> {
    run_outfiles(
        &["--pattern", "Yogi Berra", FORTUNE_DIR],
        "berra_cap.out",
        "berra_cap.err",
    )
}

#[test]
fn mark_twain_cap() -> Result<()> {
    run_outfiles(
        &["-m", "Mark Twain", FORTUNE_DIR],
        "twain_cap.out",
        "twain_cap.err",
    )
}

#[test]
fn yogi_berra_lower() -> Result<()> {
    run_outfiles(
        &["--pattern", "yogi berra", FORTUNE_DIR],
        "berra_lower.out",
        "berra_lower.err",
    )
}

#[test]
fn mark_twain_lower() -> Result<()> {
    run_outfiles(
        &["-m", "will twain", FORTUNE_DIR],
        "twain_lower.out",
        "twain_lower.err",
    )
}

#[test]
fn yogi_berra_lower_i() -> Result<()> {
    run_outfiles(
        &["--insensitive", "--pattern", "yogi berra", FORTUNE_DIR],
        "berra_lower_i.out",
        "berra_lower_i.err",
    )
}

#[test]
fn mark_twain_lower_i() -> Result<()> {
    run_outfiles(
        &["-i", "-m", "mark twain", FORTUNE_DIR],
        "twain_lower_i.out",
        "twain_lower_i.err",
    )
}
