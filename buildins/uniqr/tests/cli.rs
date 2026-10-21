use cli_testing::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
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
    out: "empty.txt.out",
    out_count: "empty.txt.c.out",
};

const ONE: Test = Test {
    input: "tests/inputs/one.txt",
    out: "one.txt.out",
    out_count: "one.txt.c.out",
};

const TWO: Test = Test {
    input: "tests/inputs/two.txt",
    out: "two.txt.out",
    out_count: "two.txt.c.out",
};

const THREE: Test = Test {
    input: "tests/inputs/three.txt",
    out: "three.txt.out",
    out_count: "three.txt.c.out",
};

const SKIP: Test = Test {
    input: "tests/inputs/skip.txt",
    out: "skip.txt.out",
    out_count: "skip.txt.c.out",
};

const T1: Test = Test {
    input: "tests/inputs/t1.txt",
    out: "t1.txt.out",
    out_count: "t1.txt.c.out",
};

const T2: Test = Test {
    input: "tests/inputs/t2.txt",
    out: "t2.txt.out",
    out_count: "t2.txt.c.out",
};

const T3: Test = Test {
    input: "tests/inputs/t3.txt",
    out: "t3.txt.out",
    out_count: "t3.txt.c.out",
};

const T4: Test = Test {
    input: "tests/inputs/t4.txt",
    out: "t4.txt.out",
    out_count: "t4.txt.c.out",
};

const T5: Test = Test {
    input: "tests/inputs/t5.txt",
    out: "t5.txt.out",
    out_count: "t5.txt.c.out",
};

const T6: Test = Test {
    input: "tests/inputs/t6.txt",
    out: "t6.txt.out",
    out_count: "t6.txt.c.out",
};

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
    let expected = cli_testing::fixtures!().read_expected(test.out)?;
    cargo_bin_cmd!()
        .arg(test.input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

fn run_count(test: &Test) -> Result<()> {
    let expected = cli_testing::fixtures!().read_expected(test.out_count)?;
    cargo_bin_cmd!()
        .args([test.input, "-c"])
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

fn run_stdin(test: &Test) -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fs::read(test.input)?;
    let expected = fixtures.read_expected(test.out)?;
    cargo_bin_cmd!()
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

fn run_stdin_count(test: &Test) -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fs::read(test.input)?;
    let expected = fixtures.read_expected(test.out_count)?;
    cargo_bin_cmd!()
        .arg("--count")
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}

fn run_outfile(test: &Test) -> Result<()> {
    let expected = cli_testing::fixtures!().read_expected(test.out)?;
    let outfile = NamedTempFile::new()?;
    cargo_bin_cmd!()
        .arg(test.input)
        .arg(outfile.path())
        .assert()
        .success()
        .stdout("")
        .stderr("");
    assert_eq!(fs::read(outfile.path())?, expected);
    Ok(())
}

fn run_outfile_count(test: &Test) -> Result<()> {
    let expected = cli_testing::fixtures!().read_expected(test.out_count)?;
    let outfile = NamedTempFile::new()?;
    cargo_bin_cmd!()
        .arg(test.input)
        .arg(outfile.path())
        .arg("--count")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    assert_eq!(fs::read(outfile.path())?, expected);
    Ok(())
}

fn run_outfile_stdin_count(test: &Test) -> Result<()> {
    let fixtures = cli_testing::fixtures!();
    let input = fs::read(test.input)?;
    let expected = fixtures.read_expected(test.out_count)?;
    let outfile = NamedTempFile::new()?;
    cargo_bin_cmd!()
        .arg("-")
        .arg(outfile.path())
        .arg("--count")
        .write_stdin(input)
        .assert()
        .success()
        .stdout("")
        .stderr("");
    assert_eq!(fs::read(outfile.path())?, expected);
    Ok(())
}

#[test]
fn rejects_unknown_flag() {
    cargo_bin_cmd!()
        .arg("--unknown")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("--unknown"));
}

#[test]
fn counts_adjacent_unicode_runs_and_unterminated_last_line() {
    cargo_bin_cmd!()
        .args(["-c", "-"])
        .write_stdin("é\né\n界\né")
        .assert()
        .success()
        .stdout("   2 é\n   1 界\n   1 é")
        .stderr("");
}

#[test]
fn counts_blank_runs_without_merging_nonadjacent_runs() {
    cargo_bin_cmd!()
        .arg("-c")
        .write_stdin("\n\nx\n\n")
        .assert()
        .success()
        .stdout("   2 \n   1 x\n   1 \n")
        .stderr("");
}

#[test]
fn trailing_spaces_and_tabs_distinguish_lines() {
    cargo_bin_cmd!()
        .arg("-c")
        .write_stdin("x\nx \nx\t\n")
        .assert()
        .success()
        .stdout("   1 x\n   1 x \n   1 x\t\n")
        .stderr("");
}

#[test]
fn output_file_is_truncated_for_shorter_result() -> Result<()> {
    let outfile = NamedTempFile::new()?;
    fs::write(outfile.path(), "stale data that must not survive")?;
    cargo_bin_cmd!()
        .arg("-")
        .arg(outfile.path())
        .write_stdin("é\né\n")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    assert_eq!(fs::read_to_string(outfile.path())?, "é\n");
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

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify observable CLI behavior; Result propagates fixture errors."
)]
fn rejects_output_aliases_without_truncating() -> Result<()> {
    let dir = assert_fs::TempDir::new()?;
    let input = dir.path().join("input");
    let alias = dir.path().join("alias");
    std::fs::write(&input, b"a\na\n")?;
    std::fs::hard_link(&input, &alias)?;
    let symlink = dir.path().join("symlink");
    std::os::unix::fs::symlink(&input, &symlink)?;
    for output in [&input, &alias, &symlink] {
        cli_testing::cargo_bin_cmd!()
            .arg(&input)
            .arg(output)
            .assert()
            .code(65)
            .stdout("")
            .stderr(predicates::str::contains("same file"));
        assert_eq!(std::fs::read(&input)?, b"a\na\n");
    }
    Ok(())
}
