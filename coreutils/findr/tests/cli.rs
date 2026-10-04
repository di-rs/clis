use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{borrow::Cow, fs};
use tempfile::TempDir;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[test]
fn usage() {
    for flag in &["-h", "--help"] {
        cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .success()
            .stdout(predicate::str::contains("Usage"));
    }
}

#[test]
fn dies_bad_name() {
    cargo_bin_cmd!()
        .args(["--name", "*.csv"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("error: invalid value '*.csv'"));
}

#[test]
fn dies_bad_type() {
    let expected = "error: invalid value 'x' for '--type [<TYPE>...]'";
    cargo_bin_cmd!()
        .args(["--type", "x"])
        .assert()
        .failure()
        .stderr(predicate::str::contains(expected));
}

#[cfg(windows)]
fn format_file_name(expected_file: &str) -> Cow<str> {
    // Equivalent to: Cow::Owned(format!("{}.windows", expected_file))
    format!("{}.windows", expected_file).into()
}

#[cfg(not(windows))]
fn format_file_name(expected_file: &str) -> Cow<'_, str> {
    // Equivalent to: Cow::Borrowed(expected_file)
    expected_file.into()
}

fn run(args: &[&str], expected_file: &str) -> Result<()> {
    let file = format_file_name(expected_file);
    let contents = fs::read_to_string(file.as_ref())?;
    let mut expected: Vec<&str> = contents.split('\n').filter(|s| !s.is_empty()).collect();
    expected.sort_unstable();

    let cmd = cargo_bin_cmd!().args(args).assert().success();
    let out = cmd.get_output();
    let stdout = String::from_utf8(out.stdout.clone())?;
    let mut lines: Vec<&str> = stdout.split('\n').filter(|s| !s.is_empty()).collect();
    lines.sort_unstable();

    assert_eq!(lines, expected);

    Ok(())
}

#[test]
fn path1() -> Result<()> {
    run(&["tests/inputs"], "tests/expected/path1.txt")
}

#[test]
fn path_a() -> Result<()> {
    run(&["tests/inputs/a"], "tests/expected/path_a.txt")
}

#[test]
fn path_a_b() -> Result<()> {
    run(&["tests/inputs/a/b"], "tests/expected/path_a_b.txt")
}

#[test]
fn path_d() -> Result<()> {
    run(&["tests/inputs/d"], "tests/expected/path_d.txt")
}

#[test]
fn path_a_b_d() -> Result<()> {
    run(
        &["tests/inputs/a/b", "tests/inputs/d"],
        "tests/expected/path_a_b_d.txt",
    )
}

#[test]
fn type_f() -> Result<()> {
    run(&["tests/inputs", "-t", "f"], "tests/expected/type_f.txt")
}

#[test]
fn type_f_path_a() -> Result<()> {
    run(
        &["tests/inputs/a", "-t", "f"],
        "tests/expected/type_f_path_a.txt",
    )
}

#[test]
fn type_f_path_a_b() -> Result<()> {
    run(
        &["tests/inputs/a/b", "--type", "f"],
        "tests/expected/type_f_path_a_b.txt",
    )
}

#[test]
fn type_f_path_d() -> Result<()> {
    run(
        &["tests/inputs/d", "--type", "f"],
        "tests/expected/type_f_path_d.txt",
    )
}

#[test]
fn type_f_path_a_b_d() -> Result<()> {
    run(
        &["tests/inputs/a/b", "tests/inputs/d", "--type", "f"],
        "tests/expected/type_f_path_a_b_d.txt",
    )
}

#[test]
fn type_d() -> Result<()> {
    run(&["tests/inputs", "-t", "d"], "tests/expected/type_d.txt")
}

#[test]
fn type_d_path_a() -> Result<()> {
    run(
        &["tests/inputs/a", "-t", "d"],
        "tests/expected/type_d_path_a.txt",
    )
}

#[test]
fn type_d_path_a_b() -> Result<()> {
    run(
        &["tests/inputs/a/b", "--type", "d"],
        "tests/expected/type_d_path_a_b.txt",
    )
}

#[test]
fn type_d_path_d() -> Result<()> {
    run(
        &["tests/inputs/d", "--type", "d"],
        "tests/expected/type_d_path_d.txt",
    )
}

#[test]
fn type_d_path_a_b_d() -> Result<()> {
    run(
        &["tests/inputs/a/b", "tests/inputs/d", "--type", "d"],
        "tests/expected/type_d_path_a_b_d.txt",
    )
}

#[test]
fn type_l() -> Result<()> {
    run(&["tests/inputs", "-t", "l"], "tests/expected/type_l.txt")
}

#[test]
fn type_f_l() -> Result<()> {
    run(
        &["tests/inputs", "-t", "l", "f"],
        "tests/expected/type_f_l.txt",
    )
}

#[test]
fn name_csv() -> Result<()> {
    run(
        &["tests/inputs", "-n", ".*[.]csv"],
        "tests/expected/name_csv.txt",
    )
}

#[test]
fn name_csv_mp3() -> Result<()> {
    run(
        &["tests/inputs", "-n", ".*[.]csv", "-n", ".*[.]mp3"],
        "tests/expected/name_csv_mp3.txt",
    )
}

#[test]
fn name_txt_path_a_d() -> Result<()> {
    run(
        &["tests/inputs/a", "tests/inputs/d", "--name", ".*.txt"],
        "tests/expected/name_txt_path_a_d.txt",
    )
}

#[test]
fn name_a() -> Result<()> {
    run(&["tests/inputs", "-n", "a"], "tests/expected/name_a.txt")
}

#[test]
fn type_f_name_a() -> Result<()> {
    run(
        &["tests/inputs", "-t", "f", "-n", "a"],
        "tests/expected/type_f_name_a.txt",
    )
}

#[test]
fn type_d_name_a() -> Result<()> {
    run(
        &["tests/inputs", "--type", "d", "--name", "a"],
        "tests/expected/type_d_name_a.txt",
    )
}

#[test]
fn path_g() -> Result<()> {
    run(&["tests/inputs/g.csv"], "tests/expected/path_g.txt")
}

#[test]
#[cfg(not(windows))]
#[allow(clippy::panic_in_result_fn)]
fn unreadable_dir() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let dir = TempDir::new()?;
    let unreadable = dir.path().join("cant-touch-this");
    fs::create_dir(&unreadable)?;
    fs::set_permissions(&unreadable, fs::Permissions::from_mode(0o000))?;

    let cmd = cargo_bin_cmd!().arg(dir.path()).assert();
    fs::set_permissions(&unreadable, fs::Permissions::from_mode(0o700))?;
    let cmd = cmd.success();

    let out = cmd.get_output();
    let stdout = String::from_utf8(out.stdout.clone())?;

    assert_eq!(stdout.lines().count(), 2);

    let stderr = String::from_utf8(out.stderr.clone())?;
    assert!(stderr.contains("cant-touch-this: Permission denied"));
    Ok(())
}

fn filter_tree() -> Result<TempDir> {
    let dir = TempDir::new()?;
    fs::create_dir(dir.path().join("reports.txt"))?;
    fs::write(dir.path().join("reports.txt/inside.csv"), "")?;
    fs::write(dir.path().join("alpha.txt"), "")?;
    fs::write(dir.path().join("beta.csv"), "")?;
    fs::write(dir.path().join(".hidden.txt"), "")?;
    Ok(dir)
}

fn run_tree(dir: &TempDir, args: &[&str], expected: &[&str]) -> Result<()> {
    let cmd = cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(args)
        .assert()
        .success()
        .stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let mut lines: Vec<_> = stdout.lines().collect();
    lines.sort_unstable();
    let mut expected: Vec<_> = expected
        .iter()
        .map(|path| path.replace('/', std::path::MAIN_SEPARATOR_STR))
        .collect();
    expected.sort_unstable();
    assert_eq!(lines, expected);
    Ok(())
}

#[test]
fn default_path_in_empty_directory() -> Result<()> {
    run_tree(&TempDir::new()?, &[], &["."])
}

#[test]
fn name_and_type_filters_intersect() -> Result<()> {
    run_tree(
        &filter_tree()?,
        &[".", "-t", "f", "-n", "[.]txt$"],
        &["./alpha.txt", "./.hidden.txt"],
    )
}

#[test]
fn name_filter_does_not_prune_directories() -> Result<()> {
    run_tree(
        &filter_tree()?,
        &[".", "-n", "^inside[.]csv$"],
        &["./reports.txt/inside.csv"],
    )
}

#[test]
fn name_matches_basename_not_parent_path() -> Result<()> {
    run_tree(
        &filter_tree()?,
        &["reports.txt", "-t", "f", "-n", "reports"],
        &[],
    )
}

#[test]
fn empty_regex_matches_all_entry_names() -> Result<()> {
    run_tree(
        &filter_tree()?,
        &[".", "-n", ""],
        &[
            ".",
            "./reports.txt",
            "./reports.txt/inside.csv",
            "./alpha.txt",
            "./beta.csv",
            "./.hidden.txt",
        ],
    )
}

#[test]
fn unmatched_name_produces_no_output() -> Result<()> {
    run_tree(&filter_tree()?, &[".", "-n", "^absent$"], &[])
}

#[test]
fn multiple_roots_apply_the_same_filter() -> Result<()> {
    run_tree(
        &filter_tree()?,
        &["alpha.txt", "reports.txt", "-t", "f", "-n", "[.]csv$"],
        &["reports.txt/inside.csv"],
    )
}

#[test]
fn invalid_regex_among_valid_names_is_rejected() {
    cargo_bin_cmd!()
        .args(["-n", "valid", "-n", "["])
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("invalid value '['"));
}

#[test]
#[cfg(unix)]
fn symlink_filter_does_not_follow_directory_targets() -> Result<()> {
    let dir = filter_tree()?;
    std::os::unix::fs::symlink("reports.txt", dir.path().join("linked"))?;
    run_tree(&dir, &[".", "-t", "l"], &["./linked"])?;
    run_tree(
        &dir,
        &[".", "-t", "f", "-n", "^inside[.]csv$"],
        &["./reports.txt/inside.csv"],
    )
}
