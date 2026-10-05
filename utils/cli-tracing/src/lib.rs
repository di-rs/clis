#![doc = include_str!("../README.md")]

use anyhow::{Context, Result};
use clap::{Args, CommandFactory};
use std::{
    io::{self, Write},
    process::ExitCode,
};
use tracing::level_filters::LevelFilter;
use tracing_subscriber::fmt::format::FmtSpan;

mod sink;

/// Shared diagnostic arguments. Flatten into the application's Clap parser.
#[derive(Args, Debug)]
pub struct LogArgs {
    /// Diagnostic threshold: off, error, warn, info, debug, trace
    #[arg(long, env = "CLIS_LOG_LEVEL", default_value = "off", value_parser = parse_level)]
    log_level: LevelFilter,
}

fn parse_level(value: &str) -> Result<LevelFilter, &'static str> {
    match value {
        "off" => Ok(LevelFilter::OFF),
        "error" => Ok(LevelFilter::ERROR),
        "warn" => Ok(LevelFilter::WARN),
        "info" => Ok(LevelFilter::INFO),
        "debug" => Ok(LevelFilter::DEBUG),
        "trace" => Ok(LevelFilter::TRACE),
        _ => Err("expected off, error, warn, info, debug or trace"),
    }
}

/// Install diagnostics, run a synchronous command, and report failures once.
///
/// `C` is the application's Clap parser: its command metadata supplies the name.
/// Call once per process, after argument parsing. Join workers before returning.
/// Returns the operation's successful exit code, or 1 for initialization,
/// operation, or diagnostic write/flush failure. Required errors bypass logging
/// filters. Domain libraries must not call this process-wide entry function.
pub fn run<C: CommandFactory>(
    args: &LogArgs,
    operation: impl FnOnce() -> Result<ExitCode>,
) -> ExitCode {
    let command = C::command();
    let name = command.get_name();
    let sink = sink::Sink::new(io::stderr());
    if let Err(error) = tracing_subscriber::fmt()
        .with_max_level(args.log_level)
        .with_ansi(false)
        .with_span_events(if args.log_level >= LevelFilter::DEBUG {
            FmtSpan::CLOSE
        } else {
            FmtSpan::NONE
        })
        .log_internal_errors(false)
        .with_writer({
            let sink = sink.clone();
            move || sink.clone()
        })
        .try_init()
        .map_err(anyhow::Error::from_boxed)
        .context("Cannot initialize diagnostics")
    {
        report(name, &error);
        return ExitCode::FAILURE;
    }

    let result = {
        let _command = tracing::debug_span!("command", command = name).entered();
        operation()
    }; // Close the command span before checking the diagnostic sink.
    let status = match result {
        Ok(status) => status,
        Err(error) => {
            report(name, &error);
            ExitCode::FAILURE
        }
    };
    if let Err(error) = sink.finish().context("Cannot finish diagnostics") {
        report(name, &error);
        return ExitCode::FAILURE;
    }
    status
}

fn report(name: &str, error: &anyhow::Error) {
    // Reporting through log::error! would suppress mandatory errors at level off.
    // A failed stderr write still leaves the caller returning a failing status.
    let _ = writeln!(io::stderr().lock(), "{name}: {error:#}");
}
