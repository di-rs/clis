use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{fs::File, io::Read, path::PathBuf};

const EMPTY: &str = "tests/inputs/empty.txt";
const ONE: &str = "tests/inputs/one.txt";
const TWO: &str = "tests/inputs/two.txt";
const THREE: &str = "tests/inputs/three.txt";
const TWELVE: &str = "tests/inputs/twelve.txt";

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
fn dies_no_args() {
    cargo_bin_cmd!()
        .assert()
        .failure()
        .stderr(predicate::str::contains("Usage"));
}

#[test]
fn dies_bad_bytes() {
    let bad = gen_bad_file();
    let expected = "invalid digit found in string";
    cargo_bin_cmd!()
        .args(["-c", &bad, EMPTY])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_bad_lines() {
    let bad = gen_bad_file();
    let expected = "invalid digit found in string";
    cargo_bin_cmd!()
        .args(["-n", &bad, EMPTY])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn dies_bytes_and_lines() {
    let msg = "the argument '--lines <LINES>' cannot be used \
               with '--bytes <BYTES>'";

    cargo_bin_cmd!()
        .args(["-n", "1", "-c", "2"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(msg));
}

#[test]
fn skips_bad_file() -> Result<()> {
    let bad = gen_bad_file();
    let expected = format!("{bad}: .* [(]os error 2[)]");
    cargo_bin_cmd!()
        .args([ONE, &bad, TWO])
        .assert()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let outfile: PathBuf = ["tests/expected", expected_file].iter().collect();
    let mut file = File::open(outfile)?;
    let mut buffer = Vec::new();
    file.read_to_end(&mut buffer)?;
    let expected = String::from_utf8_lossy(&buffer);

    let output = cargo_bin_cmd!().args(args).output()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert_eq!(stdout, expected);
    Ok(())
}

#[test]
fn empty() -> Result<()> {
    run(&[EMPTY], "empty.txt.out")
}

#[test]
fn empty_n0() -> Result<()> {
    run(&[EMPTY, "-n", "0"], "empty.txt.n0.out")
}

#[test]
fn empty_n1() -> Result<()> {
    run(&[EMPTY, "-n", "1"], "empty.txt.n1.out")
}

#[test]
fn empty_n_minus_1() -> Result<()> {
    run(&[EMPTY, "-n=-1"], "empty.txt.n1.out")
}

#[test]
fn empty_n3() -> Result<()> {
    run(&[EMPTY, "-n", "3"], "empty.txt.n3.out")
}

#[test]
fn empty_n_minus_3() -> Result<()> {
    run(&[EMPTY, "-n=-3"], "empty.txt.n3.out")
}

#[test]
fn empty_n4() -> Result<()> {
    run(&[EMPTY, "-n", "4"], "empty.txt.n4.out")
}

#[test]
fn empty_n200() -> Result<()> {
    run(&[EMPTY, "-n", "200"], "empty.txt.n200.out")
}

#[test]
fn empty_n_minus_200() -> Result<()> {
    run(&[EMPTY, "-n=-200"], "empty.txt.n200.out")
}

#[test]
fn empty_n_minus_4() -> Result<()> {
    run(&[EMPTY, "-n=-4"], "empty.txt.n4.out")
}

#[test]
fn empty_n_plus_0() -> Result<()> {
    run(&[EMPTY, "-n", "+0"], "empty.txt.n+0.out")
}

#[test]
fn empty_n_plus_1() -> Result<()> {
    run(&[EMPTY, "-n", "+1"], "empty.txt.n+1.out")
}

#[test]
fn empty_n_plus_2() -> Result<()> {
    run(&[EMPTY, "-n", "+2"], "empty.txt.n+2.out")
}

#[test]
fn empty_c3() -> Result<()> {
    run(&[EMPTY, "-c", "3"], "empty.txt.c3.out")
}

#[test]
fn empty_c_minus_3() -> Result<()> {
    run(&[EMPTY, "-c=-3"], "empty.txt.c3.out")
}

#[test]
fn empty_c8() -> Result<()> {
    run(&[EMPTY, "-c", "8"], "empty.txt.c8.out")
}

#[test]
fn empty_c_minus_8() -> Result<()> {
    run(&[EMPTY, "-c=8"], "empty.txt.c8.out")
}

#[test]
fn empty_c12() -> Result<()> {
    run(&[EMPTY, "-c", "12"], "empty.txt.c12.out")
}

#[test]
fn empty_c_minus_12() -> Result<()> {
    run(&[EMPTY, "-c=-12"], "empty.txt.c12.out")
}

#[test]
fn empty_c200() -> Result<()> {
    run(&[EMPTY, "-c", "200"], "empty.txt.c200.out")
}

#[test]
fn empty_c_minus_200() -> Result<()> {
    run(&[EMPTY, "-c=-200"], "empty.txt.c200.out")
}

#[test]
fn empty_c_plus_0() -> Result<()> {
    run(&[EMPTY, "-c", "+0"], "empty.txt.c+0.out")
}

#[test]
fn empty_c_plus_1() -> Result<()> {
    run(&[EMPTY, "-c", "+1"], "empty.txt.c+1.out")
}

#[test]
fn empty_c_plus_2() -> Result<()> {
    run(&[EMPTY, "-c", "+2"], "empty.txt.c+2.out")
}

#[test]
fn one() -> Result<()> {
    run(&[ONE], "one.txt.out")
}

#[test]
fn one_n0() -> Result<()> {
    run(&[ONE, "-n", "0"], "one.txt.n0.out")
}

#[test]
fn one_n1() -> Result<()> {
    run(&[ONE, "-n", "1"], "one.txt.n1.out")
}

#[test]
fn one_n_minus_1() -> Result<()> {
    run(&[ONE, "-n=-1"], "one.txt.n1.out")
}

#[test]
fn one_n3() -> Result<()> {
    run(&[ONE, "-n", "3"], "one.txt.n3.out")
}

#[test]
fn one_n_minus_3() -> Result<()> {
    run(&[ONE, "-n=-3"], "one.txt.n3.out")
}

#[test]
fn one_n4() -> Result<()> {
    run(&[ONE, "-n", "4"], "one.txt.n4.out")
}

#[test]
fn one_n_minus_4() -> Result<()> {
    run(&[ONE, "-n=-4"], "one.txt.n4.out")
}

#[test]
fn one_n200() -> Result<()> {
    run(&[ONE, "-n", "200"], "one.txt.n200.out")
}

#[test]
fn one_n_minus_200() -> Result<()> {
    run(&[ONE, "-n=-200"], "one.txt.n200.out")
}

#[test]
fn one_n_plus_0() -> Result<()> {
    run(&[ONE, "-n", "+0"], "one.txt.n+0.out")
}

#[test]
fn one_n_plus_1() -> Result<()> {
    run(&[ONE, "-n", "+1"], "one.txt.n+1.out")
}

#[test]
fn one_n_plus_2() -> Result<()> {
    run(&[ONE, "-n", "+2"], "one.txt.n+2.out")
}

#[test]
fn one_c3() -> Result<()> {
    run(&[ONE, "-c", "3"], "one.txt.c3.out")
}

#[test]
fn one_c_minus_3() -> Result<()> {
    run(&[ONE, "-c=-3"], "one.txt.c3.out")
}

#[test]
fn one_c8() -> Result<()> {
    run(&[ONE, "-c", "8"], "one.txt.c8.out")
}

#[test]
fn one_c_minus_8() -> Result<()> {
    run(&[ONE, "-c=8"], "one.txt.c8.out")
}

#[test]
fn one_c12() -> Result<()> {
    run(&[ONE, "-c", "12"], "one.txt.c12.out")
}

#[test]
fn one_c_minus_12() -> Result<()> {
    run(&[ONE, "-c=-12"], "one.txt.c12.out")
}

#[test]
fn one_c200() -> Result<()> {
    run(&[ONE, "-c", "200"], "one.txt.c200.out")
}

#[test]
fn one_c_minus_200() -> Result<()> {
    run(&[ONE, "-c=-200"], "one.txt.c200.out")
}

#[test]
fn one_c_plus_0() -> Result<()> {
    run(&[ONE, "-c", "+0"], "one.txt.c+0.out")
}

#[test]
fn one_c_plus_1() -> Result<()> {
    run(&[ONE, "-c", "+1"], "one.txt.c+1.out")
}

#[test]
fn one_c_plus_2() -> Result<()> {
    run(&[ONE, "-c", "+2"], "one.txt.c+2.out")
}

#[test]
fn two() -> Result<()> {
    run(&[TWO], "two.txt.out")
}

#[test]
fn two_n0() -> Result<()> {
    run(&[TWO, "-n", "0"], "two.txt.n0.out")
}

#[test]
fn two_n1() -> Result<()> {
    run(&[TWO, "-n", "1"], "two.txt.n1.out")
}

#[test]
fn two_n_minus_1() -> Result<()> {
    run(&[TWO, "-n=-1"], "two.txt.n1.out")
}

#[test]
fn two_n3() -> Result<()> {
    run(&[TWO, "-n", "3"], "two.txt.n3.out")
}

#[test]
fn two_n_minus_3() -> Result<()> {
    run(&[TWO, "-n=-3"], "two.txt.n3.out")
}

#[test]
fn two_n4() -> Result<()> {
    run(&[TWO, "-n", "4"], "two.txt.n4.out")
}

#[test]
fn two_n_minus_4() -> Result<()> {
    run(&[TWO, "-n=-4"], "two.txt.n4.out")
}

#[test]
fn two_n200() -> Result<()> {
    run(&[TWO, "-n", "200"], "two.txt.n200.out")
}

#[test]
fn two_n_minus_200() -> Result<()> {
    run(&[TWO, "-n=-200"], "two.txt.n200.out")
}

#[test]
fn two_n_plus_0() -> Result<()> {
    run(&[TWO, "-n", "+0"], "two.txt.n+0.out")
}

#[test]
fn two_n_plus_1() -> Result<()> {
    run(&[TWO, "-n", "+1"], "two.txt.n+1.out")
}

#[test]
fn two_n_plus_2() -> Result<()> {
    run(&[TWO, "-n", "+2"], "two.txt.n+2.out")
}

#[test]
fn two_c3() -> Result<()> {
    run(&[TWO, "-c", "3"], "two.txt.c3.out")
}

#[test]
fn two_c_minus_3() -> Result<()> {
    run(&[TWO, "-c=-3"], "two.txt.c3.out")
}

#[test]
fn two_c8() -> Result<()> {
    run(&[TWO, "-c", "8"], "two.txt.c8.out")
}

#[test]
fn two_c_minus_8() -> Result<()> {
    run(&[TWO, "-c=8"], "two.txt.c8.out")
}

#[test]
fn two_c12() -> Result<()> {
    run(&[TWO, "-c", "12"], "two.txt.c12.out")
}

#[test]
fn two_c_minus_12() -> Result<()> {
    run(&[TWO, "-c=-12"], "two.txt.c12.out")
}

#[test]
fn two_c200() -> Result<()> {
    run(&[TWO, "-c", "200"], "two.txt.c200.out")
}

#[test]
fn two_c_minus_200() -> Result<()> {
    run(&[TWO, "-c=-200"], "two.txt.c200.out")
}

#[test]
fn two_c_plus_0() -> Result<()> {
    run(&[TWO, "-c", "+0"], "two.txt.c+0.out")
}

#[test]
fn two_c_plus_1() -> Result<()> {
    run(&[TWO, "-c", "+1"], "two.txt.c+1.out")
}

#[test]
fn two_c_plus_2() -> Result<()> {
    run(&[TWO, "-c", "+2"], "two.txt.c+2.out")
}

#[test]
fn three() -> Result<()> {
    run(&[THREE], "three.txt.out")
}

#[test]
fn three_n0() -> Result<()> {
    run(&[THREE, "-n", "0"], "three.txt.n0.out")
}

#[test]
fn three_n1() -> Result<()> {
    run(&[THREE, "-n", "1"], "three.txt.n1.out")
}

#[test]
fn three_n_minus_1() -> Result<()> {
    run(&[THREE, "-n=-1"], "three.txt.n1.out")
}

#[test]
fn three_n3() -> Result<()> {
    run(&[THREE, "-n", "3"], "three.txt.n3.out")
}

#[test]
fn three_n_minus_3() -> Result<()> {
    run(&[THREE, "-n=-3"], "three.txt.n3.out")
}

#[test]
fn three_n4() -> Result<()> {
    run(&[THREE, "-n", "4"], "three.txt.n4.out")
}

#[test]
fn three_n_minus_4() -> Result<()> {
    run(&[THREE, "-n=-4"], "three.txt.n4.out")
}

#[test]
fn three_n200() -> Result<()> {
    run(&[THREE, "-n", "200"], "three.txt.n200.out")
}

#[test]
fn three_n_minus_200() -> Result<()> {
    run(&[THREE, "-n=-200"], "three.txt.n200.out")
}

#[test]
fn three_n_plus_0() -> Result<()> {
    run(&[THREE, "-n", "+0"], "three.txt.n+0.out")
}

#[test]
fn three_n_plus_1() -> Result<()> {
    run(&[THREE, "-n", "+1"], "three.txt.n+1.out")
}

#[test]
fn three_n_plus_2() -> Result<()> {
    run(&[THREE, "-n", "+2"], "three.txt.n+2.out")
}

#[test]
fn three_c3() -> Result<()> {
    run(&[THREE, "-c", "3"], "three.txt.c3.out")
}

#[test]
fn three_c_minus_3() -> Result<()> {
    run(&[THREE, "-c=-3"], "three.txt.c3.out")
}

#[test]
fn three_c8() -> Result<()> {
    run(&[THREE, "-c", "8"], "three.txt.c8.out")
}

#[test]
fn three_c_minus_8() -> Result<()> {
    run(&[THREE, "-c=8"], "three.txt.c8.out")
}

#[test]
fn three_c12() -> Result<()> {
    run(&[THREE, "-c", "12"], "three.txt.c12.out")
}

#[test]
fn three_c_minus_12() -> Result<()> {
    run(&[THREE, "-c=-12"], "three.txt.c12.out")
}

#[test]
fn three_c200() -> Result<()> {
    run(&[THREE, "-c", "200"], "three.txt.c200.out")
}

#[test]
fn three_c_minus_200() -> Result<()> {
    run(&[THREE, "-c=-200"], "three.txt.c200.out")
}

#[test]
fn three_c_plus_0() -> Result<()> {
    run(&[THREE, "-c", "+0"], "three.txt.c+0.out")
}

#[test]
fn three_c_plus_1() -> Result<()> {
    run(&[THREE, "-c", "+1"], "three.txt.c+1.out")
}

#[test]
fn three_c_plus_2() -> Result<()> {
    run(&[THREE, "-c", "+2"], "three.txt.c+2.out")
}

#[test]
fn twelve() -> Result<()> {
    run(&[TWELVE], "twelve.txt.out")
}

#[test]
fn twelve_n0() -> Result<()> {
    run(&[TWELVE, "-n", "0"], "twelve.txt.n0.out")
}

#[test]
fn twelve_n1() -> Result<()> {
    run(&[TWELVE, "-n", "1"], "twelve.txt.n1.out")
}

#[test]
fn twelve_n_minus_1() -> Result<()> {
    run(&[TWELVE, "-n=-1"], "twelve.txt.n1.out")
}

#[test]
fn twelve_n3() -> Result<()> {
    run(&[TWELVE, "-n", "3"], "twelve.txt.n3.out")
}

#[test]
fn twelve_n_minus_3() -> Result<()> {
    run(&[TWELVE, "-n=-3"], "twelve.txt.n3.out")
}

#[test]
fn twelve_n4() -> Result<()> {
    run(&[TWELVE, "-n", "4"], "twelve.txt.n4.out")
}

#[test]
fn twelve_n_minus_4() -> Result<()> {
    run(&[TWELVE, "-n=-4"], "twelve.txt.n4.out")
}

#[test]
fn twelve_n200() -> Result<()> {
    run(&[TWELVE, "-n", "200"], "twelve.txt.n200.out")
}

#[test]
fn twelve_n_minus_200() -> Result<()> {
    run(&[TWELVE, "-n=-200"], "twelve.txt.n200.out")
}

#[test]
fn twelve_c3() -> Result<()> {
    run(&[TWELVE, "-c", "3"], "twelve.txt.c3.out")
}

#[test]
fn twelve_c_minus_3() -> Result<()> {
    run(&[TWELVE, "-c=-3"], "twelve.txt.c3.out")
}

#[test]
fn twelve_c8() -> Result<()> {
    run(&[TWELVE, "-c", "8"], "twelve.txt.c8.out")
}

#[test]
fn twelve_c_minus_8() -> Result<()> {
    run(&[TWELVE, "-c=8"], "twelve.txt.c8.out")
}

#[test]
fn twelve_c12() -> Result<()> {
    run(&[TWELVE, "-c", "12"], "twelve.txt.c12.out")
}

#[test]
fn twelve_c_minus_12() -> Result<()> {
    run(&[TWELVE, "-c=-12"], "twelve.txt.c12.out")
}

#[test]
fn twelve_c200() -> Result<()> {
    run(&[TWELVE, "-c", "200"], "twelve.txt.c200.out")
}

#[test]
fn twelve_c_minus_200() -> Result<()> {
    run(&[TWELVE, "-c=-200"], "twelve.txt.c200.out")
}

#[test]
fn twelve_n_plus_0() -> Result<()> {
    run(&[TWELVE, "-n", "+0"], "twelve.txt.n+0.out")
}

#[test]
fn twelve_n_plus_1() -> Result<()> {
    run(&[TWELVE, "-n", "+1"], "twelve.txt.n+1.out")
}

#[test]
fn twelve_n_plus_2() -> Result<()> {
    run(&[TWELVE, "-n", "+2"], "twelve.txt.n+2.out")
}

#[test]
fn twelve_c_plus_0() -> Result<()> {
    run(&[TWELVE, "-c", "+0"], "twelve.txt.c+0.out")
}

#[test]
fn twelve_c_plus_1() -> Result<()> {
    run(&[TWELVE, "-c", "+1"], "twelve.txt.c+1.out")
}

#[test]
fn twelve_c_plus_2() -> Result<()> {
    run(&[TWELVE, "-c", "+2"], "twelve.txt.c+2.out")
}

#[test]
fn multiple_files() -> Result<()> {
    run(&[TWELVE, EMPTY, ONE, THREE, TWO], "all.out")
}

#[test]
fn multiple_files_n0() -> Result<()> {
    run(&["-n", "0", TWELVE, EMPTY, ONE, THREE, TWO], "all.n0.out")
}

#[test]
fn multiple_files_n1() -> Result<()> {
    run(&["-n", "1", TWELVE, EMPTY, ONE, THREE, TWO], "all.n1.out")
}

#[test]
fn multiple_files_n1_q() -> Result<()> {
    run(
        &["-n", "1", "-q", TWELVE, EMPTY, ONE, THREE, TWO],
        "all.n1.q.out",
    )
}

#[test]
fn multiple_files_n1_quiet() -> Result<()> {
    run(
        &["-n", "1", "--quiet", TWELVE, EMPTY, ONE, THREE, TWO],
        "all.n1.q.out",
    )
}

#[test]
fn multiple_files_n_minus_1() -> Result<()> {
    run(&["-n=-1", TWELVE, EMPTY, ONE, THREE, TWO], "all.n1.out")
}

#[test]
fn multiple_files_n_plus_1() -> Result<()> {
    run(&["-n", "+1", TWELVE, EMPTY, ONE, THREE, TWO], "all.n+1.out")
}

#[test]
fn multiple_files_n3() -> Result<()> {
    run(&["-n", "3", TWELVE, EMPTY, ONE, THREE, TWO], "all.n3.out")
}

#[test]
fn multiple_files_n_minus_3() -> Result<()> {
    run(&["-n=-3", TWELVE, EMPTY, ONE, THREE, TWO], "all.n3.out")
}

#[test]
fn multiple_files_n_plus_3() -> Result<()> {
    run(&["-n", "+3", TWELVE, EMPTY, ONE, THREE, TWO], "all.n+3.out")
}

#[test]
fn multiple_files_c0() -> Result<()> {
    run(&["-c", "0", TWELVE, EMPTY, ONE, THREE, TWO], "all.c0.out")
}

#[test]
fn multiple_files_c3() -> Result<()> {
    run(&["-c", "3", TWELVE, EMPTY, ONE, THREE, TWO], "all.c3.out")
}

#[test]
fn multiple_files_c_minus_3() -> Result<()> {
    run(&["-c=-3", TWELVE, EMPTY, ONE, THREE, TWO], "all.c3.out")
}

#[test]
fn multiple_files_c_plus_3() -> Result<()> {
    run(&["-c", "+3", TWELVE, EMPTY, ONE, THREE, TWO], "all.c+3.out")
}
