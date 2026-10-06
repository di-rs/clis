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

/// Global diagnostic arguments for CLIs with subcommands.
///
/// Capture the effective value first, then call [`Self::resolve`] after Clap has
/// propagated subcommand flags. This lets an explicit flag override an invalid
/// environment value without reparsing argv or initializing diagnostics.
#[derive(Args, Debug)]
pub struct GlobalLogArgs {
    /// Diagnostic threshold: off, error, warn, info, debug, trace
    #[arg(
        short = 'L',
        long,
        global = true,
        env = "CLIS_LOG_LEVEL",
        default_value = "off"
    )]
    log_level: std::ffi::OsString,
}

impl GlobalLogArgs {
    /// Validate the effective level and produce configuration for [`run`].
    ///
    /// # Errors
    /// Returns a diagnostic for unsupported or non-UTF-8 levels. The CLI should
    /// report this as a Clap value-validation error before invoking [`run`].
    pub fn resolve(&self) -> Result<LogArgs, &'static str> {
        let value = self.log_level.to_str().ok_or("--log-level must be UTF-8")?;
        parse_level(value)
            .map(|log_level| LogArgs { log_level })
            .map_err(|_| "invalid --log-level: expected off, error, warn, info, debug or trace")
    }
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
