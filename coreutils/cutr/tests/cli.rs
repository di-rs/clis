use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use rand::RngExt;
use rand::distr::Alphanumeric;
use std::fs;
use std::path::PathBuf;

const CSV: &str = "tests/inputs/movies1.csv";
const TSV: &str = "tests/inputs/movies1.tsv";
const BOOKS: &str = "tests/inputs/books.tsv";

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

fn random_string() -> String {
    rand::rng()
        .sample_iter(&Alphanumeric)
        .take(7)
        .map(char::from)
        .collect()
}

fn gen_bad_file() -> String {
    loop {
        let filename = random_string();
        if fs::metadata(&filename).is_err() {
            return filename;
        }
    }
}

#[test]
fn skips_bad_file() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("{bad}: .* [(]os error 2[)]");
    cargo_bin_cmd!()
        .args(["-f", "1", CSV, &bad, TSV])
        .assert()
        .success()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

fn dies(args: &[&str], expected: &str) {
    cargo_bin_cmd!()
        .args(args)
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_not_enough_args() {
    dies(
        &[CSV],
        "the following required arguments were not provided:\n  \
        <--fields <FIELDS>|--bytes <BYTES>|--chars <CHARS>>",
    );
}

#[test]
fn dies_bad_digit_field() {
    let bad = random_string();
    dies(&[CSV, "-f", &bad], &format!("illegal list value: `{bad}`"));
}

#[test]
fn dies_bad_digit_bytes() {
    let bad = random_string();
    dies(&[CSV, "-b", &bad], &format!("illegal list value: `{bad}`"));
}

#[test]
fn dies_bad_digit_chars() {
    let bad = random_string();
    dies(&[CSV, "-c", &bad], &format!("illegal list value: `{bad}`"));
}

#[test]
fn dies_empty_delimiter() {
    dies(
        &[CSV, "-f", "1", "-d", ""],
        "--delim `` must be a single byte",
    );
}

#[test]
fn dies_bad_delimiter() {
    dies(
        &[CSV, "-f", "1", "-d", ",,"],
        "--delim `,,` must be a single byte",
    );
}

#[test]
fn dies_chars_bytes_fields() {
    cargo_bin_cmd!()
        .args([CSV, "-c", "1", "-f", "1", "-b", "1"])
        .assert()
        .failure();
}

#[test]
fn dies_bytes_fields() {
    cargo_bin_cmd!()
        .args([CSV, "-f", "1", "-b", "1"])
        .assert()
        .failure();
}

#[test]
fn dies_chars_fields() {
    cargo_bin_cmd!()
        .args([CSV, "-c", "1", "-f", "1"])
        .assert()
        .failure();
}

#[test]
fn dies_chars_bytes() {
    cargo_bin_cmd!()
        .args([CSV, "-c", "1", "-b", "1"])
        .assert()
        .failure();
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let expected = fs::read_to_string(outfile)?;
    let cmd = cargo_bin_cmd!().args(args).assert().success().stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    assert_eq!(stdout, expected);
    Ok(())
}

fn run_lossy(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let contents = fs::read(outfile)?;
    let expected = String::from_utf8_lossy(&contents);
    let cmd = cargo_bin_cmd!().args(args).assert().success().stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    assert_eq!(stdout, expected);
    Ok(())
}

#[test]
fn stdin_chars_keep_requested_order_and_repetitions() {
    cargo_bin_cmd!()
        .args(["-c", "3,1-2,1,9"])
        .write_stdin("é界x\n\n猫")
        .assert()
        .success()
        .stdout("xé界é\n\n猫猫\n")
        .stderr("");
}

#[test]
fn stdin_bytes_use_lossy_utf8_at_split_boundaries() {
    for (positions, expected) in [("1", "�\n"), ("1-2", "é\n"), ("9", "\n")] {
        cargo_bin_cmd!()
            .args(["-b", positions, "-"])
            .write_stdin("éx")
            .assert()
            .success()
            .stdout(expected)
            .stderr("");
    }
}

#[test]
fn stdin_fields_parse_quoted_delimiters_and_empty_fields() {
    cargo_bin_cmd!()
        .args(["-d", ",", "-f", "2,1,3"])
        .write_stdin("\"a,b\",,c\n\"é\n界\",x,y")
        .assert()
        .success()
        .stdout(",\"a,b\",c\nx,\"é\n界\",y\n")
        .stderr("");
}

#[test]
fn empty_stdin_in_each_mode() {
    for mode in ["-f", "-b", "-c"] {
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
fn rejects_invalid_lists_and_multibyte_delimiter() {
    for positions in ["", "0", "2-1", "1,", "+1"] {
        cargo_bin_cmd!()
            .args(["-c", positions])
            .write_stdin("abc")
            .assert()
            .failure()
            .stdout("")
            .stderr(predicate::str::contains("Error:"));
    }
    dies(&["-f", "1", "-d", "é"], "must be a single byte");
    dies(&["--unknown"], "--unknown");
}

#[test]
fn tsv_f1() -> Result<()> {
    run(&[TSV, "-f", "1"], "movies1.tsv.f1.out")
}

#[test]
fn tsv_f2() -> Result<()> {
    run(&[TSV, "-f", "2"], "movies1.tsv.f2.out")
}

#[test]
fn tsv_f3() -> Result<()> {
    run(&[TSV, "-f", "3"], "movies1.tsv.f3.out")
}

#[test]
fn tsv_f1_2() -> Result<()> {
    run(&[TSV, "-f", "1-2"], "movies1.tsv.f1-2.out")
}

#[test]
fn tsv_f2_3() -> Result<()> {
    run(&[TSV, "-f", "2-3"], "movies1.tsv.f2-3.out")
}

#[test]
fn tsv_f1_3() -> Result<()> {
    run(&[TSV, "-f", "1-3"], "movies1.tsv.f1-3.out")
}

#[test]
fn csv_f1() -> Result<()> {
    run(&[CSV, "-f", "1", "-d", ","], "movies1.csv.f1.dcomma.out")
}

#[test]
fn csv_f2() -> Result<()> {
    run(&[CSV, "-f", "2", "-d", ","], "movies1.csv.f2.dcomma.out")
}

#[test]
fn csv_f3() -> Result<()> {
    run(&[CSV, "-f", "3", "-d", ","], "movies1.csv.f3.dcomma.out")
}

#[test]
fn csv_f1_2() -> Result<()> {
    run(
        &[CSV, "-f", "1-2", "-d", ","],
        "movies1.csv.f1-2.dcomma.out",
    )
}

#[test]
fn csv_f2_3() -> Result<()> {
    run(
        &[CSV, "-f", "2-3", "-d", ","],
        "movies1.csv.f2-3.dcomma.out",
    )
}

#[test]
fn csv_f1_3() -> Result<()> {
    run(
        &[CSV, "-f", "1-3", "-d", ","],
        "movies1.csv.f1-3.dcomma.out",
    )
}

#[test]
fn tsv_b1() -> Result<()> {
    run(&[TSV, "-b", "1"], "movies1.tsv.b1.out")
}

#[test]
fn tsv_b2() -> Result<()> {
    run(&[TSV, "-b", "2"], "movies1.tsv.b2.out")
}

#[test]
fn tsv_b8() -> Result<()> {
    run_lossy(&[TSV, "-b", "8"], "movies1.tsv.b8.out")
}

#[test]
fn tsv_b1_2() -> Result<()> {
    run(&[TSV, "-b", "1-2"], "movies1.tsv.b1-2.out")
}

#[test]
fn tsv_b2_3() -> Result<()> {
    run(&[TSV, "-b", "2-3"], "movies1.tsv.b2-3.out")
}

#[test]
fn tsv_b1_8() -> Result<()> {
    run_lossy(&[TSV, "-b", "1-8"], "movies1.tsv.b1-8.out")
}

#[test]
fn tsv_c1() -> Result<()> {
    run(&[TSV, "-c", "1"], "movies1.tsv.c1.out")
}

#[test]
fn tsv_c2() -> Result<()> {
    run(&[TSV, "-c", "2"], "movies1.tsv.c2.out")
}

#[test]
fn tsv_c8() -> Result<()> {
    run(&[TSV, "-c", "8"], "movies1.tsv.c8.out")
}

#[test]
fn tsv_c1_2() -> Result<()> {
    run(&[TSV, "-c", "1-2"], "movies1.tsv.c1-2.out")
}

#[test]
fn tsv_c2_3() -> Result<()> {
    run(&[TSV, "-c", "2-3"], "movies1.tsv.c2-3.out")
}

#[test]
fn tsv_c1_8() -> Result<()> {
    run(&[TSV, "-c", "1-8"], "movies1.tsv.c1-8.out")
}

#[test]
fn repeated_value() -> Result<()> {
    run(&[BOOKS, "-c", "1,1"], "books.c1,1.out")
}
