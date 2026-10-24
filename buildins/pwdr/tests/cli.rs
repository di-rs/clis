use assert_fs::{TempDir, prelude::*};
use cli_testing::{Command, cargo_bin_cmd};
use predicates::prelude::*;
use std::path::Path;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn command(cwd: &Path) -> Command {
    let mut cmd = cargo_bin_cmd!();
    cmd.current_dir(cwd).env_remove("PWD");
    cmd
}

fn assert_path(cmd: &mut Command, expected: &Path) {
    cmd.assert()
        .success()
        .stdout(format!("{}\n", expected.display()))
        .stderr("");
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
fn prints_current_directory_in_each_mode() -> Result<()> {
    let dir = TempDir::new()?;
    let physical = dir.path().canonicalize()?;
    for args in [&[][..], &["-L"][..], &["-P"][..]] {
        assert_path(
            command(&physical).env("PWD", &physical).args(args),
            &physical,
        );
    }
    Ok(())
}

#[test]
fn falls_back_when_pwd_is_unset() -> Result<()> {
    let dir = TempDir::new()?;
    let physical = dir.path().canonicalize()?;
    for args in [&[][..], &["-L"][..]] {
        assert_path(command(&physical).args(args), &physical);
    }
    Ok(())
}

#[test]
fn falls_back_when_pwd_is_invalid() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("cwd").create_dir_all()?;
    dir.child("other").create_dir_all()?;
    dir.child("file").write_str("not a directory")?;
    let physical = dir.child("cwd").path().canonicalize()?;
    for pwd in [
        "".into(),
        "relative".into(),
        dir.path().join("missing"),
        dir.path().join("other"),
        dir.path().join("file"),
    ] {
        for args in [&[][..], &["-L"][..], &["-P"][..]] {
            assert_path(command(&physical).env("PWD", &pwd).args(args), &physical);
        }
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn logical_modes_preserve_symlink_pwd() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("real").create_dir_all()?;
    let physical = dir.child("real").path().canonicalize()?;
    let logical = dir.path().canonicalize()?.join("link");
    std::os::unix::fs::symlink(&physical, &logical)?;
    for args in [&[][..], &["-L"][..]] {
        assert_path(command(&logical).env("PWD", &logical).args(args), &logical);
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn physical_mode_resolves_symlink_pwd() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("real").create_dir_all()?;
    let physical = dir.child("real").path().canonicalize()?;
    let logical = dir.path().canonicalize()?.join("link");
    std::os::unix::fs::symlink(&physical, &logical)?;
    assert_path(command(&logical).env("PWD", &logical).arg("-P"), &physical);
    Ok(())
}

#[test]
fn rejects_conflicting_modes() -> Result<()> {
    let dir = TempDir::new()?;
    for args in [["-L", "-P"], ["-P", "-L"]] {
        command(dir.path())
            .args(args)
            .assert()
            .code(2)
            .stdout("")
            .stderr(predicate::str::contains("cannot be used with"));
    }
    Ok(())
}

#[test]
fn exports_public_cli_schema_without_application_side_effects()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    let logs = assert_fs::TempDir::new()?;
    let output = cli_testing::cargo_bin_cmd!()
        .env("SANCTUARY_CLI_SCHEMA", "1")
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_DIR", logs.path())
        .arg("--help")
        .assert()
        .code(0)
        .stderr("")
        .get_output()
        .stdout
        .clone();
    let value: serde_json::Value = serde_json::from_slice(&output)?;
    if value["schema_version"] != 1 {
        return Err("unexpected schema version".into());
    }
    if value["command"]["name"] != env!("CARGO_PKG_NAME") {
        return Err("unexpected CLI identity".into());
    }
    if !value["command"]["args"].is_array() {
        return Err("missing argument metadata".into());
    }
    if std::fs::read_dir(logs.path())?.count() != 0 {
        return Err("schema export wrote logs".into());
    }
    Ok(())
}
