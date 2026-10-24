use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use rand::RngExt;
use rand::distr::Alphanumeric;
use std::fs;

const CSV: &str = "tests/inputs/movies1.csv";
const TSV: &str = "tests/inputs/movies1.tsv";

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
        .code(1)
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
fn missing_operand_returns_failure() {
    cli_testing::cargo_bin_cmd!()
        .args(["-b", "1", "/sanctuary-audit-missing/path"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("/sanctuary-audit-missing/path"));
}

#[cli_testing::test_case("tsv_b1_8")]
fn tsv_b1_8(cx: &mut cli_testing::Context) -> cli_testing::Result<()> {
    // The reference emits raw bytes; cutr replaces incomplete UTF-8 sequences.
    cx.map_expected_stdout(|bytes| String::from_utf8_lossy(bytes).into_owned().into_bytes())?;
    Ok(())
}
