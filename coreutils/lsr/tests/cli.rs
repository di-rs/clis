use assert_cmd::cargo::cargo_bin_cmd;
use assert_fs::{TempDir, prelude::*};
use predicates::prelude::*;
use pretty_assertions::assert_eq;
use std::{fs, os::unix::fs::PermissionsExt};

const HIDDEN: &str = "tests/inputs/.hidden";
const EMPTY: &str = "tests/inputs/empty.txt";
const BUSTLE: &str = "tests/inputs/bustle.txt";
const FOX: &str = "tests/inputs/fox.txt";

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn fixture_tree() -> Result<TempDir> {
    let dir = TempDir::new()?;
    let nested = dir.child("tests/inputs/dir");
    nested.create_dir_all()?;
    fs::set_permissions(nested.path(), fs::Permissions::from_mode(0o755))?;
    for (source, mode) in [
        (EMPTY, 0o644),
        (BUSTLE, 0o644),
        (FOX, 0o600),
        (HIDDEN, 0o644),
        ("tests/inputs/dir/spiders.txt", 0o644),
        ("tests/inputs/dir/.gitkeep", 0o644),
    ] {
        let destination = dir.child(source);
        fs::copy(source, destination.path())?;
        fs::set_permissions(destination.path(), fs::Permissions::from_mode(mode))?;
    }
    Ok(dir)
}

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

fn gen_bad_file() -> String {
    "tests/file/doesnt/exist".to_owned()
}

#[test]
fn bad_file() {
    let bad = gen_bad_file();
    let expected = format!("{bad}: No such file or directory (os error 2)");
    cargo_bin_cmd!()
        .arg(&bad)
        .assert()
        .success()
        .stderr(predicate::str::contains(expected));
}

#[test]
fn no_args() {
    cargo_bin_cmd!()
        .assert()
        .success()
        .stdout(predicate::str::contains("Cargo.toml"));
}

fn run_short(arg: &str) {
    cargo_bin_cmd!()
        .arg(arg)
        .assert()
        .success()
        .stdout(format!("{arg}\n"));
}

fn run_long(filename: &str, permissions: &str, size: &str) -> Result<()> {
    let dir = fixture_tree()?;
    let cmd = cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["--long", filename])
        .assert()
        .success()
        .stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let parts: Vec<_> = stdout.split_whitespace().collect();
    assert_eq!(parts.first().copied(), Some(permissions));
    assert_eq!(parts.get(4).copied(), Some(size));
    assert_eq!(parts.last().copied(), Some(filename));
    Ok(())
}

#[test]
fn empty() {
    run_short(EMPTY);
}

#[test]
fn empty_long() -> Result<()> {
    run_long(EMPTY, "-rw-r--r--", "0")
}

#[test]
fn bustle() {
    run_short(BUSTLE);
}

#[test]
fn bustle_long() -> Result<()> {
    run_long(BUSTLE, "-rw-r--r--", "193")
}

#[test]
fn fox() {
    run_short(FOX);
}

#[test]
fn fox_long() -> Result<()> {
    run_long(FOX, "-rw-------", "45")
}

#[test]
fn hidden() {
    run_short(HIDDEN);
}

#[test]
fn hidden_long() -> Result<()> {
    run_long(HIDDEN, "-rw-r--r--", "0")
}

fn dir_short(args: &[&str], expected: &[&str]) -> Result<()> {
    let cmd = cargo_bin_cmd!().args(args).assert().success();
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let lines: Vec<&str> = stdout.split('\n').filter(|s| !s.is_empty()).collect();
    assert_eq!(lines.len(), expected.len());
    for filename in expected {
        assert_eq!(true, lines.contains(filename));
    }
    Ok(())
}

#[test]
fn dir1() -> Result<()> {
    dir_short(
        &["tests/inputs"],
        &[
            "tests/inputs/empty.txt",
            "tests/inputs/bustle.txt",
            "tests/inputs/fox.txt",
            "tests/inputs/dir",
        ],
    )
}

#[test]
fn dir1_all() -> Result<()> {
    dir_short(
        &["tests/inputs", "--all"],
        &[
            "tests/inputs/empty.txt",
            "tests/inputs/bustle.txt",
            "tests/inputs/fox.txt",
            "tests/inputs/.hidden",
            "tests/inputs/dir",
        ],
    )
}

#[test]
fn dir2() -> Result<()> {
    dir_short(&["tests/inputs/dir"], &["tests/inputs/dir/spiders.txt"])
}

#[test]
fn dir2_all() -> Result<()> {
    dir_short(
        &["-a", "tests/inputs/dir"],
        &["tests/inputs/dir/spiders.txt", "tests/inputs/dir/.gitkeep"],
    )
}

fn dir_long(args: &[&str], expected: &[(&str, &str, &str)]) -> Result<()> {
    let dir = fixture_tree()?;
    let cmd = cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(args)
        .assert()
        .success()
        .stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let lines: Vec<&str> = stdout.split('\n').filter(|s| !s.is_empty()).collect();
    assert_eq!(lines.len(), expected.len());

    let mut check = vec![];
    for line in lines {
        let parts: Vec<&str> = line.split_whitespace().collect();
        let path = parts.last().copied().ok_or("missing path in listing")?;
        let permissions = parts
            .first()
            .copied()
            .ok_or("missing permissions in listing")?;
        let size = match permissions.chars().next() {
            Some('d') => "",
            _ => parts.get(4).copied().ok_or("missing size in listing")?,
        };
        check.push((path, permissions, size));
    }

    for entry in expected {
        assert_eq!(true, check.contains(entry));
    }

    Ok(())
}

#[test]
fn dir1_long() -> Result<()> {
    dir_long(
        &["-l", "tests/inputs"],
        &[
            ("tests/inputs/empty.txt", "-rw-r--r--", "0"),
            ("tests/inputs/bustle.txt", "-rw-r--r--", "193"),
            ("tests/inputs/fox.txt", "-rw-------", "45"),
            ("tests/inputs/dir", "drwxr-xr-x", ""),
        ],
    )
}

#[test]
fn dir1_long_all() -> Result<()> {
    dir_long(
        &["-la", "tests/inputs"],
        &[
            ("tests/inputs/empty.txt", "-rw-r--r--", "0"),
            ("tests/inputs/bustle.txt", "-rw-r--r--", "193"),
            ("tests/inputs/fox.txt", "-rw-------", "45"),
            ("tests/inputs/dir", "drwxr-xr-x", ""),
            ("tests/inputs/.hidden", "-rw-r--r--", "0"),
        ],
    )
}

#[test]
fn dir2_long() -> Result<()> {
    dir_long(
        &["--long", "tests/inputs/dir"],
        &[("tests/inputs/dir/spiders.txt", "-rw-r--r--", "45")],
    )
}

#[test]
fn dir2_long_all() -> Result<()> {
    dir_long(
        &["tests/inputs/dir", "--long", "--all"],
        &[
            ("tests/inputs/dir/spiders.txt", "-rw-r--r--", "45"),
            ("tests/inputs/dir/.gitkeep", "-rw-r--r--", "0"),
        ],
    )
}

fn check_listing(dir: &TempDir, args: &[&str], expected: &[&str]) -> Result<()> {
    let cmd = cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(args)
        .assert()
        .success()
        .stderr("");
    let stdout = String::from_utf8(cmd.get_output().stdout.clone())?;
    let mut lines: Vec<_> = stdout.lines().collect();
    lines.sort_unstable();
    let mut expected = expected.to_vec();
    expected.sort_unstable();
    assert_eq!(lines, expected);
    Ok(())
}

#[test]
fn empty_directory() -> Result<()> {
    check_listing(&TempDir::new()?, &["."], &[])
}

#[test]
fn default_path_hides_dotfiles() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("visible").touch()?;
    dir.child(".hidden").touch()?;
    check_listing(&dir, &[], &["./visible"])
}

#[test]
fn hidden_only_directory_requires_all() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child(".hidden").touch()?;
    check_listing(&dir, &["."], &[])?;
    check_listing(&dir, &["--all", "."], &["./.hidden"])
}

#[test]
fn explicit_hidden_file_does_not_require_all() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child(".hidden").touch()?;
    check_listing(&dir, &[".hidden"], &[".hidden"])
}

#[test]
fn multiple_files_and_directory_are_not_recursive() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("folder/nested").create_dir_all()?;
    dir.child("folder/nested/not-listed").touch()?;
    dir.child("folder/child").touch()?;
    dir.child("first").touch()?;
    dir.child("last").touch()?;
    check_listing(
        &dir,
        &["first", "folder", "last"],
        &["first", "folder/child", "folder/nested", "last"],
    )
}

#[test]
fn filename_with_spaces_is_preserved() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("two words.txt");
    file.write_str("hello")?;
    fs::set_permissions(file.path(), fs::Permissions::from_mode(0o640))?;
    check_listing(&dir, &["two words.txt"], &["two words.txt"])?;
    cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["-l", "two words.txt"])
        .assert()
        .success()
        .stderr("")
        .stdout(predicate::str::starts_with("-rw-r-----"))
        .stdout(predicate::str::ends_with(" two words.txt\n\n"));
    Ok(())
}

#[test]
fn missing_path_does_not_hide_valid_files() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("present").touch()?;
    cargo_bin_cmd!()
        .current_dir(dir.path())
        .args(["missing", "present"])
        .assert()
        .success()
        .stdout("present\n")
        .stderr(predicate::str::contains(
            "missing: No such file or directory",
        ));
    Ok(())
}
