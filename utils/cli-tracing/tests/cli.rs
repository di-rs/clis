#![allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify child-process behavior; Result propagates I/O errors."
)]

use anyhow::{Context, Result};
use clap::Parser;
use std::{
    process::{Command, ExitCode, Output},
    sync::atomic::{AtomicBool, Ordering},
};

#[derive(Parser)]
#[command(name = "renamed-command")]
struct Cli {
    #[command(flatten)]
    logging: cli_tracing::LogArgs,
}

fn child(case: &str, level: &str) -> Result<Output> {
    Ok(Command::new(std::env::current_exe()?)
        .args(["--exact", "driver", "--nocapture"])
        .env("CLIS_TEST_CASE", case)
        .env("CLIS_LOG_LEVEL", level)
        .output()?)
}

// Global initialization is tested in isolated processes, not reset between tests.
#[test]
fn driver() -> Result<()> {
    let Ok(case) = std::env::var("CLIS_TEST_CASE") else {
        return Ok(());
    };
    let cli = Cli::try_parse_from(["arbitrary-argv-zero"])?;
    let exit = cli_tracing::run::<Cli>(&cli.logging, || {
        match case.as_str() {
            "failure" => return Err(std::io::Error::other("root cause")).context("opening input"),
            "status" => return Ok(ExitCode::from(7)),
            "duplicate" => {
                let inner = cli_tracing::run::<Cli>(&cli.logging, || {
                    println!("must not run");
                    Ok(ExitCode::SUCCESS)
                });
                assert_eq!(inner, ExitCode::FAILURE);
            }
            _ => {
                let evaluated = AtomicBool::new(false);
                {
                    let _stage = tracing::debug_span!(
                        "work",
                        records = {
                            evaluated.store(true, Ordering::Relaxed);
                            3
                        }
                    )
                    .entered();
                    log::error!("error-record");
                    log::warn!("warn-record");
                    log::info!("info-record");
                    log::debug!("debug-record");
                    log::trace!("trace-record");
                    tracing::info!("native-record");
                    std::thread::scope(|scope| {
                        scope.spawn(|| log::info!("worker-record"));
                    });
                }
                assert_eq!(
                    evaluated.load(Ordering::Relaxed),
                    cli_tracing_level_is_debug()
                );
            }
        }
        Ok(ExitCode::SUCCESS)
    });
    let expected = match case.as_str() {
        "failure" | "sink" => ExitCode::FAILURE,
        "status" => ExitCode::from(7),
        _ => ExitCode::SUCCESS,
    };
    assert_eq!(exit, expected);
    Ok(())
}

fn cli_tracing_level_is_debug() -> bool {
    matches!(
        std::env::var("CLIS_LOG_LEVEL").as_deref(),
        Ok("debug" | "trace")
    )
}

#[test]
fn bridge_filters_all_levels_and_collects_worker_events() -> Result<()> {
    let messages = [
        "error-record",
        "warn-record",
        "info-record",
        "debug-record",
        "trace-record",
    ];
    for (level, count) in [
        ("off", 0),
        ("error", 1),
        ("warn", 2),
        ("info", 3),
        ("debug", 4),
        ("trace", 5),
    ] {
        let output = child("events", level)?;
        assert!(output.status.success(), "{output:?}");
        let stderr = String::from_utf8(output.stderr)?;
        for (index, message) in messages.iter().enumerate() {
            assert_eq!(
                stderr.matches(message).count(),
                usize::from(index < count),
                "{level}: {stderr}"
            );
        }
        assert_eq!(
            stderr.matches("native-record").count(),
            usize::from(count >= 3)
        );
        assert_eq!(
            stderr.matches("worker-record").count(),
            usize::from(count >= 3)
        );
        assert_eq!(stderr.contains("time.busy="), count >= 4);
        assert_eq!(stderr.contains("time.idle="), count >= 4);
        assert!(!stderr.contains('\u{1b}'));
        if count >= 4 {
            assert!(stderr.contains("command=\"renamed-command\""));
            assert!(stderr.contains("work{records=3}"));
        }
    }
    Ok(())
}

#[test]
fn failures_use_clap_name_and_keep_causes_without_duplicate_logging() -> Result<()> {
    for level in ["off", "error", "trace"] {
        let output = child("failure", level)?;
        assert!(output.status.success(), "{output:?}");
        let stderr = String::from_utf8(output.stderr)?;
        assert_eq!(
            stderr
                .matches("renamed-command: opening input: root cause")
                .count(),
            1
        );
        assert!(!stderr.contains("arbitrary-argv-zero:"));
    }
    Ok(())
}

#[test]
fn expected_nonzero_status_is_preserved_without_error_message() -> Result<()> {
    let output = child("status", "off")?;
    assert!(output.status.success(), "{output:?}");
    assert_eq!(output.stderr, b"");
    Ok(())
}

#[test]
fn repeated_installation_fails_before_operation_without_panicking() -> Result<()> {
    let output = child("duplicate", "off")?;
    assert!(output.status.success(), "{output:?}");
    assert!(!String::from_utf8(output.stdout)?.contains("must not run"));
    assert!(
        String::from_utf8(output.stderr)?
            .contains("renamed-command: Cannot initialize diagnostics")
    );
    Ok(())
}

#[cfg(unix)]
#[test]
fn stderr_write_failure_cannot_report_success() -> Result<()> {
    // A disconnected stream yields BrokenPipe (stdio silently ignores EBADF).
    let (reader, writer) = std::os::unix::net::UnixStream::pair()?;
    drop(reader);
    let output = Command::new(std::env::current_exe()?)
        .args(["--exact", "driver", "--nocapture"])
        .env("CLIS_TEST_CASE", "sink")
        .env("CLIS_LOG_LEVEL", "info")
        .stderr(std::os::fd::OwnedFd::from(writer))
        .output()?;
    assert!(output.status.success(), "{output:?}");
    Ok(())
}
