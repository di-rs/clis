#![allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify adapter behavior in child processes; Result propagates I/O errors."
)]

use anyhow::Result;
use clap::Parser;
use serde_json::Value;
use std::{
    io::Write,
    path::Path,
    process::{Command, Output},
};

fn command(root: &Path, case: &str, level: &str) -> Result<Command> {
    let mut command = Command::new(std::env::current_exe()?);
    command
        .args(["--exact", "driver", "--nocapture"])
        .env("CLIS_TEST_CASE", case)
        .env("CLIS_LOG_DIR", root)
        .env("CLIS_LOG_LEVEL", level)
        .env("CLIS_LOG_STDERR", "off")
        .env_remove("CLIS_LOG_SESSION");
    Ok(command)
}
fn child(root: &Path, case: &str, level: &str) -> Result<Output> {
    let output = command(root, case, level)?.output()?;
    assert!(output.status.success(), "{output:?}");
    Ok(output)
}
fn records(root: &Path) -> Result<Vec<Value>> {
    let mut records = Vec::new();
    for entry in std::fs::read_dir(root)? {
        let path = entry?.path();
        if path.extension().is_some_and(|ext| ext == "jsonl") {
            let text = std::fs::read_to_string(path)?;
            for line in text.lines() {
                records.push(serde_json::from_str(line)?);
            }
        }
    }
    Ok(records)
}
#[derive(Parser)]
struct Cli {
    #[arg(long)]
    count: usize,
}

#[test]
fn driver() -> Result<()> {
    let Ok(case) = std::env::var("CLIS_TEST_CASE") else {
        return Ok(());
    };
    if case == "panic" {
        std::panic::set_hook(Box::new(|_| eprintln!("previous-hook")));
    }
    let session = cli_log::init("example");
    let status = match case.as_str() {
        "events" => {
            log::error!("error-record");
            log::warn!("warn-record");
            log::info!(answer = 42, ready = true; "info\nrecord");
            log::debug!("debug-record");
            log::trace!("trace-record");
            std::thread::scope(|scope| {
                scope.spawn(|| log::info!("worker-record"));
            });
            0
        }
        "failure" => {
            cli_log::diagnostic!("example: opening input: root cause");
            65
        }
        "parse" => {
            let error = Cli::try_parse_from(["example", "--count", "invalid"])
                .err()
                .ok_or_else(|| anyhow::anyhow!("expected argument error"))?;
            assert_eq!(session.clap_error(&error), std::process::ExitCode::from(2));
            return Ok(());
        }
        "panic" => {
            let _ = std::panic::catch_unwind(|| {
                panic!("panic-record");
            });
            101
        }
        "duplicate" => {
            let _ = cli_log::init("other").finish(0);
            writeln!(std::io::stdout().lock(), "hello")?;
            0
        }
        "output" => {
            writeln!(std::io::stdout().lock(), "hello")?;
            0
        }
        _ => 1,
    };
    assert_eq!(
        session.finish(status),
        std::process::ExitCode::from(u8::try_from(status)?)
    );
    Ok(())
}

#[test]
fn backend_json_preserves_fields_and_invocation_context() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let output = command(root.path(), "events", "info")?
        .env("CLIS_LOG_SESSION", "shell-123")
        .output()?;
    assert!(output.status.success());
    assert_eq!(output.stderr, b"");
    let events = records(root.path())?;
    let start = events
        .iter()
        .find(|v| v.pointer("/kv/event") == Some(&Value::from("command_started")))
        .ok_or_else(|| anyhow::anyhow!("missing start"))?;
    assert_eq!(start.pointer("/kv/command"), Some(&Value::from("example")));
    assert_eq!(
        start.pointer("/kv/session_id"),
        Some(&Value::from("shell-123"))
    );
    let id = start
        .pointer("/kv/invocation_id")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("missing invocation id"))?;
    assert!(
        root.path()
            .join(format!("example_{id}_rCURRENT.jsonl"))
            .is_file()
    );
    let info = events
        .iter()
        .find(|v| v.get("text") == Some(&Value::from("info\nrecord")))
        .ok_or_else(|| anyhow::anyhow!("missing info event"))?;
    assert_eq!(info.get("level"), Some(&Value::from("INFO")));
    assert_eq!(info.pointer("/kv/answer"), Some(&Value::from(42)));
    assert_eq!(info.pointer("/kv/ready"), Some(&Value::Bool(true)));
    assert!(
        events
            .iter()
            .any(|v| v.get("text") == Some(&Value::from("worker-record")))
    );
    Ok(())
}

#[test]
fn file_thresholds_filter_events_and_off_creates_no_files() -> Result<()> {
    for (level, count) in [
        ("off", 0),
        ("error", 1),
        ("warn", 2),
        ("info", 4),
        ("debug", 5),
        ("trace", 6),
    ] {
        let root = assert_fs::TempDir::new()?;
        assert_eq!(child(root.path(), "events", level)?.stderr, b"");
        let events = records(root.path())?;
        assert_eq!(
            events
                .iter()
                .filter(|v| v.pointer("/kv/event").is_none())
                .count(),
            count
        );
        if level == "off" {
            assert_eq!(std::fs::read_dir(root.path())?.count(), 0);
        }
    }
    Ok(())
}

#[test]
fn required_errors_print_once_and_nonzero_status_is_preserved() -> Result<()> {
    for level in ["off", "error", "trace"] {
        let root = assert_fs::TempDir::new()?;
        let output = command(root.path(), "failure", level)?
            .env("CLIS_LOG_STDERR", "trace")
            .output()?;
        assert!(output.status.success());
        assert_eq!(
            String::from_utf8(output.stderr)?,
            "example: opening input: root cause\n"
        );
        let events = records(root.path())?;
        if level != "off" {
            assert!(
                events
                    .iter()
                    .any(|v| v.pointer("/kv/event") == Some(&Value::from("error")))
            );
            assert!(
                events
                    .iter()
                    .any(|v| v.pointer("/kv/exit_code") == Some(&Value::from(65)))
            );
        }
    }
    let root = assert_fs::TempDir::new()?;
    child(root.path(), "status", "error")?;
    let events = records(root.path())?;
    assert_eq!(events.len(), 2);
    assert!(
        events
            .iter()
            .any(|v| v.pointer("/kv/exit_code") == Some(&Value::from(1)))
    );
    Ok(())
}

#[test]
fn argument_errors_and_panics_retain_original_reporting() -> Result<()> {
    for (case, event, code) in [("parse", "argument_error", 2), ("panic", "panic", 101)] {
        let root = assert_fs::TempDir::new()?;
        let output = child(root.path(), case, "info")?;
        let events = records(root.path())?;
        assert!(
            events
                .iter()
                .any(|v| v.pointer("/kv/event") == Some(&Value::from(event)))
        );
        assert!(
            events
                .iter()
                .any(|v| v.pointer("/kv/exit_code") == Some(&Value::from(code)))
        );
        if case == "panic" {
            assert!(String::from_utf8(output.stderr)?.contains("previous-hook"));
        }
    }
    Ok(())
}

#[test]
fn unavailable_storage_does_not_fail_the_command() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let path = root.path().join("not-a-directory");
    std::fs::write(&path, "keep")?;
    let output = child(&path, "events", "trace")?;
    assert!(String::from_utf8(output.stderr)?.contains("logging warning"));
    assert_eq!(std::fs::read_to_string(path)?, "keep");
    Ok(())
}

#[test]
fn setup_warnings_do_not_panic_when_stderr_is_closed() -> Result<()> {
    for (case, level, console, blocked) in [
        ("output", "invalid", "off", false),
        ("output", "info", "invalid", false),
        ("output", "info", "off", true),
        ("duplicate", "info", "off", false),
    ] {
        let root = assert_fs::TempDir::new()?;
        let path = root.path().join("logs");
        if blocked {
            std::fs::write(&path, "keep")?;
        }
        let (reader, writer) = std::io::pipe()?;
        drop(reader);
        let output = command(&path, case, level)?
            .env("CLIS_LOG_STDERR", console)
            .stderr(writer)
            .output()?;
        assert!(
            output.status.success(),
            "{case}/{level}/{console}: {output:?}"
        );
        assert!(String::from_utf8(output.stdout)?.contains("hello"));
        if blocked {
            assert_eq!(std::fs::read_to_string(path)?, "keep");
        }
    }
    Ok(())
}

#[test]
fn concurrent_invocations_have_separate_complete_logs() -> Result<()> {
    let root = assert_fs::TempDir::new()?;
    let mut children = Vec::new();
    for _ in 0..4 {
        children.push(command(root.path(), "events", "info")?.spawn()?);
    }
    for mut child in children {
        assert!(child.wait()?.success());
    }
    assert_eq!(std::fs::read_dir(root.path())?.count(), 4);
    assert_eq!(records(root.path())?.len(), 24);
    Ok(())
}
