#![allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify environment configuration in isolated child processes."
)]

use anyhow::Result;
use serde_json::Value;
use std::{
    path::{Path, PathBuf},
    process::{Command, Output},
};

fn command(root: &Path) -> Result<Command> {
    let mut command = Command::new(std::env::current_exe()?);
    command
        .args(["--exact", "driver", "--nocapture"])
        .current_dir(root)
        .env("SANCTUARY_CONFIG_TEST", "1")
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_STDERR", "off")
        .env("HOME", root.join("home"))
        .env("LOCALAPPDATA", root.join("local"))
        .env_remove("SANCTUARY_LOG_DIR")
        .env_remove("XDG_STATE_HOME")
        .env_remove("SANCTUARY_LOG_SESSION");
    Ok(command)
}

fn run(command: &mut Command) -> Result<Output> {
    let output = command.output()?;
    assert!(output.status.success(), "{output:?}");
    Ok(output)
}

fn records(root: &Path) -> Result<Vec<Value>> {
    let mut records = Vec::new();
    for entry in std::fs::read_dir(root)? {
        let path = entry?.path();
        if path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().starts_with("config-test_"))
            && path
                .extension()
                .is_some_and(|extension| extension == "jsonl")
        {
            for line in std::fs::read_to_string(path)?.lines() {
                records.push(serde_json::from_str(line)?);
            }
        }
    }
    Ok(records)
}

fn has_message(records: &[Value], message: &str) -> bool {
    records
        .iter()
        .any(|record| record.get("text").and_then(Value::as_str) == Some(message))
}

#[cfg(not(windows))]
fn default_root(root: &Path) -> PathBuf {
    if cfg!(target_os = "macos") {
        root.join("home/Library/Logs/sanctuary")
    } else {
        root.join("home/.local/state/sanctuary/logs")
    }
}

#[cfg(windows)]
fn default_root(root: &Path) -> PathBuf {
    root.join("local/sanctuary/logs")
}

#[test]
fn driver() {
    if std::env::var_os("SANCTUARY_CONFIG_TEST").is_none() {
        return;
    }
    let session = cli_log::init("config-test");
    log::error!("error-message");
    log::info!("info-message");
    log::debug!("debug-message");
    let _ = session.finish(0);
}

#[test]
fn directory_override_takes_precedence_over_platform_defaults() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let selected = root.path().join("selected");
    let output = run(command(root.path())?
        .env("SANCTUARY_LOG_DIR", &selected)
        .env("XDG_STATE_HOME", root.path().join("xdg")))?;
    assert_eq!(output.stderr, b"");
    assert!(has_message(&records(&selected)?, "info-message"));
    assert!(!default_root(root.path()).exists());
    assert!(!root.path().join("xdg").exists());
    Ok(())
}

#[cfg(not(windows))]
#[test]
fn absolute_xdg_state_home_selects_persistent_state_directory() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let selected = root.path().join("state");
    let output = run(command(root.path())?.env("XDG_STATE_HOME", &selected))?;
    assert_eq!(output.stderr, b"");
    assert!(has_message(
        &records(&selected.join("sanctuary/logs"))?,
        "info-message"
    ));
    assert!(!default_root(root.path()).exists());
    Ok(())
}

#[test]
fn default_directory_uses_platform_home_and_ignores_relative_xdg() -> Result<()> {
    for xdg in [None, Some("relative-state")] {
        let root = assert_fs::TempDir::new()?;
        let mut command = command(root.path())?;
        if let Some(xdg) = xdg {
            command.env("XDG_STATE_HOME", xdg);
        }
        let output = run(&mut command)?;
        assert_eq!(output.stderr, b"");
        assert!(has_message(
            &records(&default_root(root.path()))?,
            "info-message"
        ));
        assert!(!root.path().join("relative-state").exists());
    }
    Ok(())
}

#[test]
fn unavailable_home_warns_once_and_preserves_command_success() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let output = run(command(root.path())?
        .env_remove("HOME")
        .env_remove("LOCALAPPDATA"))?;
    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr.matches("logging warning").count(), 1);
    assert!(stderr.contains("no log directory"));
    assert_eq!(std::fs::read_dir(root.path())?.count(), 0);
    Ok(())
}

#[test]
fn invalid_file_level_warns_once_and_falls_back_to_info() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let output = run(command(root.path())?
        .env("SANCTUARY_LOG_DIR", root.path())
        .env("SANCTUARY_LOG_LEVEL", "invalid"))?;
    let stderr = String::from_utf8(output.stderr)?;
    assert_eq!(stderr.matches("logging warning").count(), 1);
    assert!(stderr.contains("SANCTUARY_LOG_LEVEL"));
    let events = records(root.path())?;
    assert!(has_message(&events, "info-message"));
    assert!(!has_message(&events, "debug-message"));
    Ok(())
}

#[test]
fn console_threshold_is_independent_of_file_threshold() -> Result<()> {
    for level in ["off", "error"] {
        let root = assert_fs::TempDir::new()?;
        let output = run(command(root.path())?
            .env("SANCTUARY_LOG_DIR", root.path())
            .env("SANCTUARY_LOG_LEVEL", level)
            .env("SANCTUARY_LOG_STDERR", "info"))?;
        let stderr = String::from_utf8(output.stderr)?;
        assert!(stderr.contains("info-message"));
        assert!(stderr.contains("error-message"));
        assert!(!stderr.contains("debug-message"));
        if level == "off" {
            assert_eq!(std::fs::read_dir(root.path())?.count(), 0);
        } else {
            let events = records(root.path())?;
            assert!(has_message(&events, "error-message"));
            assert!(!has_message(&events, "info-message"));
        }
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn newly_created_log_directories_are_private() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let root = assert_fs::TempDir::new()?;
    let parent = root.path().join("private");
    let logs = parent.join("logs");
    let output = run(command(root.path())?.env("SANCTUARY_LOG_DIR", &logs))?;
    assert_eq!(output.stderr, b"");
    for path in [&parent, &logs] {
        assert_eq!(std::fs::metadata(path)?.permissions().mode() & 0o777, 0o700);
    }
    assert!(has_message(&records(&logs)?, "info-message"));
    Ok(())
}

#[cfg(unix)]
#[test]
fn existing_log_directory_permissions_are_preserved() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let root = assert_fs::TempDir::new()?;
    let logs = root.path().join("existing");
    std::fs::create_dir(&logs)?;
    std::fs::set_permissions(&logs, std::fs::Permissions::from_mode(0o750))?;
    let output = run(command(root.path())?.env("SANCTUARY_LOG_DIR", &logs))?;
    assert_eq!(output.stderr, b"");
    assert_eq!(
        std::fs::metadata(&logs)?.permissions().mode() & 0o777,
        0o750
    );
    assert!(has_message(&records(&logs)?, "info-message"));
    Ok(())
}
