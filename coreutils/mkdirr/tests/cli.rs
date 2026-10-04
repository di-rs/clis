use assert_cmd::{Command, cargo::cargo_bin_cmd};
use assert_fs::{TempDir, prelude::*};
use predicates::prelude::*;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn command(dir: &TempDir) -> Command {
    let mut cmd = cargo_bin_cmd!();
    cmd.current_dir(dir.path());
    cmd
}

#[test]
fn usage() {
    for flag in ["-h", "--help"] {
        cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .success()
            .stdout(predicate::str::contains("Usage"))
            .stderr("");
    }
}

#[test]
fn creates_single_directory() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .arg("new")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("new").assert(predicate::path::is_dir());
    Ok(())
}

#[test]
fn creates_multiple_directories() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["one", "two", "three"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    for name in ["one", "two", "three"] {
        dir.child(name).assert(predicate::path::is_dir());
    }
    Ok(())
}

#[test]
fn creates_nested_directories_by_default() -> Result<()> {
    // mkdirr currently defaults parent creation to true, unlike standard mkdir.
    let dir = TempDir::new()?;
    command(&dir)
        .arg("one/two/three")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("one/two/three").assert(predicate::path::is_dir());
    Ok(())
}

#[test]
fn creates_nested_directories_with_p() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["-p", "one/two/three"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("one/two/three").assert(predicate::path::is_dir());
    Ok(())
}

#[test]
fn p_accepts_existing_directory() -> Result<()> {
    // Regression: parent creation should tolerate a directory already present.
    let dir = TempDir::new()?;
    dir.child("existing").create_dir_all()?;
    dir.child("existing/contents").write_str("keep me")?;
    command(&dir)
        .args(["-p", "existing"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("existing/contents").assert("keep me");
    Ok(())
}

#[test]
fn rejects_existing_file() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("existing").write_str("keep me")?;
    command(&dir)
        .arg("existing")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("existing"));
    dir.child("existing").assert("keep me");
    Ok(())
}

#[test]
fn rejects_file_as_parent() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("file").write_str("keep me")?;
    command(&dir)
        .arg("file/child")
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::contains("file/child"));
    dir.child("file").assert("keep me");
    Ok(())
}

#[test]
fn requires_directory_operand() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::is_empty().not());
    Ok(())
}
