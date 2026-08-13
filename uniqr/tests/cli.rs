use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
// use pretty_assertions::assert_eq;
use std::{fs, os::unix::fs::PermissionsExt};
use tempfile::NamedTempFile;

struct Test {
    input: &'static str,
    out: &'static str,
    out_count: &'static str,
}

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

const EMPTY: Test = Test {
    input: "tests/inputs/empty.txt",
    out: "tests/expected/empty.txt.out",
    out_count: "tests/expected/empty.txt.c.out",
};

const ONE: Test = Test {
    input: "tests/inputs/one.txt",
    out: "tests/expected/one.txt.out",
    out_count: "tests/expected/one.txt.c.out",
};

const TWO: Test = Test {
    input: "tests/inputs/two.txt",
    out: "tests/expected/two.txt.out",
    out_count: "tests/expected/two.txt.c.out",
};

const THREE: Test = Test {
    input: "tests/inputs/three.txt",
    out: "tests/expected/three.txt.out",
    out_count: "tests/expected/three.txt.c.out",
};

const SKIP: Test = Test {
    input: "tests/inputs/skip.txt",
    out: "tests/expected/skip.txt.out",
    out_count: "tests/expected/skip.txt.c.out",
};

const T1: Test = Test {
    input: "tests/inputs/t1.txt",
    out: "tests/expected/t1.txt.out",
    out_count: "tests/expected/t1.txt.c.out",
};

const T2: Test = Test {
    input: "tests/inputs/t2.txt",
    out: "tests/expected/t2.txt.out",
    out_count: "tests/expected/t2.txt.c.out",
};

const T3: Test = Test {
    input: "tests/inputs/t3.txt",
    out: "tests/expected/t3.txt.out",
    out_count: "tests/expected/t3.txt.c.out",
};

const T4: Test = Test {
    input: "tests/inputs/t4.txt",
    out: "tests/expected/t4.txt.out",
    out_count: "tests/expected/t4.txt.c.out",
};

const T5: Test = Test {
    input: "tests/inputs/t5.txt",
    out: "tests/expected/t5.txt.out",
    out_count: "tests/expected/t5.txt.c.out",
};

const T6: Test = Test {
    input: "tests/inputs/t6.txt",
    out: "tests/expected/t6.txt.out",
    out_count: "tests/expected/t6.txt.c.out",
};

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
    let file = NamedTempFile::new()?;
    fs::set_permissions(file.path(), PermissionsExt::from_mode(0o000))?;
    Ok(file)
}

#[test]
fn dies_bad_file() -> Result<()> {
    let bad = gen_bad_file()?;
    let expected = format!("{}: .* [(]os error 13[)]", bad.path().display());
    cargo_bin_cmd!()
        .arg(bad.path())
        .assert()
        .failure()
        .stderr(predicate::str::is_match(expected)?);
    Ok(())
}

fn run(test: &Test) -> Result<()> {
    let expected = std::fs::read_to_string(test.out)?;
    cargo_bin_cmd!()
        .arg(test.input)
        .assert()
        .success()
        .stdout(expected);
    Ok(())
}

fn run_count(test: &Test) -> Result<()> {
    let expected = std::fs::read_to_string(test.out_count)?;
    cargo_bin_cmd!()
        .args([test.input, "-c"])
        .assert()
        .success()
        .stdout(expected);
    Ok(())
}

fn run_stdin(test: &Test) -> Result<()> {
    let input = fs::read_to_string(test.input)?;
    let expected = std::fs::read_to_string(test.out)?;
    
    cargo_bin_cmd!()
        .write_stdin(input)
        .assert()
        .success()
        .stdout(expected);
    Ok(())
}

fn run_stdin_count(test: &Test) -> Result<()> {
    let input = fs::read_to_string(test.input)?;
    let expected = std::fs::read_to_string(test.out_count)?;
    
    cargo_bin_cmd!()
        .arg("--count")
        .write_stdin(input)
        .assert()
        .success()
        .stdout(expected);
    Ok(())
}

fn run_outfile(test: &Test) -> Result<()> {
    let expected = std::fs::read_to_string(test.out)?;
    let outfile = NamedTempFile::new()?;
    let outpath = outfile.path().to_str().unwrap_or_default();

    cargo_bin_cmd!()
        .args([test.input, outpath])
        .assert()
        .success()
        .stdout("");
    let contents = fs::read_to_string(outpath)?;
    assert_eq!(expected, contents);
    Ok(())
}

fn run_outfile_count(test: &Test) -> Result<()> {
    let expected = fs::read_to_string(test.out_count)?;
    let outfile = NamedTempFile::new()?;
    let outpath = outfile.path().to_str().unwrap_or_default();

    cargo_bin_cmd!()
        .args([test.input, outpath, "--count"])
        .assert()
        .success()
        .stdout("");
    let contents = fs::read_to_string(outpath)?;
    assert_eq!(expected, contents);
    Ok(())
}

fn run_outfile_stdin_count(test: &Test) -> Result<()> {
    let input = std::fs::read_to_string(test.input)?;
    let expected = fs::read_to_string(test.out_count)?;
    let outfile = NamedTempFile::new()?;
    let outpath = outfile.path().to_str().unwrap_or_default();

    cargo_bin_cmd!()
        .args(["-", outpath, "--count"])
        .write_stdin(input)
        .assert()
        .success()
        .stdout("");
    let contents = fs::read_to_string(outpath)?;
    assert_eq!(expected, contents);
    Ok(())
}

#[test]
fn empty() -> Result<()> {
    run(&EMPTY)
}

#[test]
fn empty_count() -> Result<()> {
    run_count(&EMPTY)
}

#[test]
fn empty_stdin() -> Result<()> {
    run_stdin(&EMPTY)
}

#[test]
fn empty_stdin_count() -> Result<()> {
    run_stdin_count(&EMPTY)
}

#[test]
fn empty_outfile() -> Result<()> {
    run_outfile(&EMPTY)
}

#[test]
fn empty_outfile_count() -> Result<()> {
    run_outfile_count(&EMPTY)
}

#[test]
fn empty_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&EMPTY)
}

#[test]
fn one() -> Result<()> {
    run(&ONE)
}

#[test]
fn one_count() -> Result<()> {
    run_count(&ONE)
}

#[test]
fn one_stdin() -> Result<()> {
    run_stdin(&ONE)
}

#[test]
fn one_stdin_count() -> Result<()> {
    run_stdin_count(&ONE)
}

#[test]
fn one_outfile() -> Result<()> {
    run_outfile(&ONE)
}

#[test]
fn one_outfile_count() -> Result<()> {
    run_outfile_count(&ONE)
}

#[test]
fn one_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&ONE)
}

#[test]
fn two() -> Result<()> {
    run(&TWO)
}

#[test]
fn two_count() -> Result<()> {
    run_count(&TWO)
}

#[test]
fn two_stdin() -> Result<()> {
    run_stdin(&TWO)
}

#[test]
fn two_stdin_count() -> Result<()> {
    run_stdin_count(&TWO)
}

#[test]
fn two_outfile() -> Result<()> {
    run_outfile(&TWO)
}

#[test]
fn two_outfile_count() -> Result<()> {
    run_outfile_count(&TWO)
}

#[test]
fn two_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&TWO)
}

#[test]
fn three() -> Result<()> {
    run(&THREE)
}

#[test]
fn three_count() -> Result<()> {
    run_count(&THREE)
}

#[test]
fn three_stdin() -> Result<()> {
    run_stdin(&THREE)
}

#[test]
fn three_stdin_count() -> Result<()> {
    run_stdin_count(&THREE)
}

#[test]
fn three_outfile() -> Result<()> {
    run_outfile(&THREE)
}

#[test]
fn three_outfile_count() -> Result<()> {
    run_outfile_count(&THREE)
}

#[test]
fn three_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&THREE)
}

#[test]
fn skip() -> Result<()> {
    run(&SKIP)
}

#[test]
fn skip_count() -> Result<()> {
    run_count(&SKIP)
}

#[test]
fn skip_stdin() -> Result<()> {
    run_stdin(&SKIP)
}

#[test]
fn skip_stdin_count() -> Result<()> {
    run_stdin_count(&SKIP)
}

#[test]
fn skip_outfile() -> Result<()> {
    run_outfile(&SKIP)
}

#[test]
fn skip_outfile_count() -> Result<()> {
    run_outfile_count(&SKIP)
}

#[test]
fn skip_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&SKIP)
}

#[test]
fn t1() -> Result<()> {
    run(&T1)
}

#[test]
fn t1_count() -> Result<()> {
    run_count(&T1)
}

#[test]
fn t1_stdin() -> Result<()> {
    run_stdin(&T1)
}

#[test]
fn t1_stdin_count() -> Result<()> {
    run_stdin_count(&T1)
}

#[test]
fn t1_outfile() -> Result<()> {
    run_outfile(&T1)
}

#[test]
fn t1_outfile_count() -> Result<()> {
    run_outfile_count(&T1)
}

#[test]
fn t1_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&T1)
}

#[test]
fn t2() -> Result<()> {
    run(&T2)
}

#[test]
fn t2_count() -> Result<()> {
    run_count(&T2)
}

#[test]
fn t2_stdin() -> Result<()> {
    run_stdin(&T2)
}

#[test]
fn t2_stdin_count() -> Result<()> {
    run_stdin_count(&T2)
}

#[test]
fn t2_outfile() -> Result<()> {
    run_outfile(&T2)
}

#[test]
fn t2_outfile_count() -> Result<()> {
    run_outfile_count(&T2)
}

#[test]
fn t2_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&T2)
}

#[test]
fn t3() -> Result<()> {
    run(&T3)
}

#[test]
fn t3_count() -> Result<()> {
    run_count(&T3)
}

#[test]
fn t3_stdin() -> Result<()> {
    run_stdin(&T3)
}

#[test]
fn t3_stdin_count() -> Result<()> {
    run_stdin_count(&T3)
}

#[test]
fn t3_outfile() -> Result<()> {
    run_outfile(&T3)
}

#[test]
fn t3_outfile_count() -> Result<()> {
    run_outfile_count(&T3)
}

#[test]
fn t3_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&T3)
}

#[test]
fn t4() -> Result<()> {
    run(&T4)
}

#[test]
fn t4_count() -> Result<()> {
    run_count(&T4)
}

#[test]
fn t4_stdin() -> Result<()> {
    run_stdin(&T4)
}

#[test]
fn t4_stdin_count() -> Result<()> {
    run_stdin_count(&T4)
}

#[test]
fn t4_outfile() -> Result<()> {
    run_outfile(&T4)
}

#[test]
fn t4_outfile_count() -> Result<()> {
    run_outfile_count(&T4)
}

#[test]
fn t4_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&T4)
}

#[test]
fn t5() -> Result<()> {
    run(&T5)
}

#[test]
fn t5_count() -> Result<()> {
    run_count(&T5)
}

#[test]
fn t5_stdin() -> Result<()> {
    run_stdin(&T5)
}

#[test]
fn t5_stdin_count() -> Result<()> {
    run_stdin_count(&T5)
}

#[test]
fn t5_outfile() -> Result<()> {
    run_outfile(&T5)
}

#[test]
fn t5_outfile_count() -> Result<()> {
    run_outfile_count(&T5)
}

#[test]
fn t5_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&T5)
}

#[test]
fn t6() -> Result<()> {
    run(&T6)
}

#[test]
fn t6_count() -> Result<()> {
    run_count(&T6)
}

#[test]
fn t6_stdin() -> Result<()> {
    run_stdin(&T6)
}

#[test]
fn t6_stdin_count() -> Result<()> {
    run_stdin_count(&T6)
}

#[test]
fn t6_outfile() -> Result<()> {
    run_outfile(&T6)
}

#[test]
fn t6_outfile_count() -> Result<()> {
    run_outfile_count(&T6)
}

#[test]
fn t6_stdin_outfile_count() -> Result<()> {
    run_outfile_stdin_count(&T6)
}
