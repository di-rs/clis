#![doc = include_str!("../README.md")]

use flexi_logger::{
    Cleanup, Criterion, DeferredNow, FileSpec, Logger, LoggerHandle, Naming, WriteMode,
    filter::{LogLineFilter, LogLineWriter},
    writers::FileLogWriter,
};
use log::{LevelFilter, Record};
use std::{
    fmt::{self, Display},
    io::{self, Write},
    process::ExitCode,
    sync::OnceLock,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

mod config;
pub use config::{GlobalLogArgs, LogArgs};
static FILE_ENABLED: OnceLock<bool> = OnceLock::new();
const FILE_TARGET: &str = "{file}";

/// Keeps the backend alive and records completion. Initialize once in each binary.
pub struct Session {
    handle: Option<LoggerHandle>,
    file_level: LevelFilter,
    started: Instant,
}

/// Initialize logging, parse arguments, and record the operation's exit status.
///
/// The operation owns command-specific diagnostics and status mapping. Clap
/// help/version and argument failures finish the session without calling it.
pub fn run<C: clap::Parser>(command: &str, operation: impl FnOnce(C) -> i32) -> ExitCode {
    let session = init(command);
    match session.parse::<C>() {
        Ok(cli) => session.finish(operation(cli)),
        Err(status) => status,
    }
}

/// Run a fallible command, preserving its error for the caller's rich reporter.
///
/// Install reporting/panic hooks before calling this helper. Success records
/// status 0; operation errors are logged without printing and record status 1.
/// Clap exits retain their original status and skip the operation.
///
/// # Errors
/// Returns the original operation error, including its context chain.
pub fn try_run<C: clap::Parser, E: Display>(
    command: &str,
    operation: impl FnOnce(C) -> Result<(), E>,
) -> Result<ExitCode, E> {
    let session = init(command);
    let cli = match session.parse::<C>() {
        Ok(cli) => cli,
        Err(status) => return Ok(status),
    };
    match operation(cli) {
        Ok(()) => Ok(session.finish(0)),
        Err(error) => {
            record_error(format_args!("{error:#}"));
            let _ = session.finish(1);
            Err(error)
        }
    }
}

/// Configure the crates.io backend from `SANCTUARY_LOG_*`, without failing the CLI.
#[must_use]
pub fn init(command: &str) -> Session {
    let (level, warning) = config::env_level("SANCTUARY_LOG_LEVEL", LevelFilter::Info);
    if let Some(warning) = warning {
        warn(command, warning);
    }
    init_with_level(command, level)
}

/// Configure the backend with a file threshold overriding the environment.
#[must_use]
pub fn init_with_level(command: &str, level: LevelFilter) -> Session {
    let started = Instant::now();
    let (console, warning) = config::env_level("SANCTUARY_LOG_STDERR", LevelFilter::Off);
    if let Some(warning) = warning {
        warn(command, warning);
    }
    let mut logger = Logger::with(level.max(console))
        .do_not_log()
        .duplicate_to_stderr(console.into())
        .panic_if_error_channel_is_broken(false);
    let invocation = format!(
        "{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let mut file_level = LevelFilter::Off;
    if level != LevelFilter::Off {
        let file = config::log_dir()
            .map_err(|error| error.to_string())
            .and_then(|root| {
                FileLogWriter::builder(
                    FileSpec::default()
                        .directory(root)
                        .basename(command)
                        .discriminant(&invocation)
                        .suppress_timestamp()
                        .suffix("jsonl"),
                )
                .format(flexi_logger::json_format)
                .write_mode(WriteMode::Direct)
                .rotate(
                    Criterion::Size(5 * 1024 * 1024),
                    Naming::Numbers,
                    Cleanup::KeepLogFiles(3),
                )
                .cleanup_in_background_thread(false)
                .try_build()
                .map_err(|error| error.to_string())
            });
        match file {
            Ok(file) => {
                logger = logger.add_writer("file", Box::new(file));
                file_level = level;
            }
            Err(error) => warn(command, error),
        }
    }
    let handle = match logger.filter(Box::new(FileRouting(file_level))).start() {
        Ok(handle) => {
            let _ = FILE_ENABLED.set(file_level != LevelFilter::Off);
            if file_enabled() {
                log::info!(target: FILE_TARGET, event = "command_started", command,
                    pid = std::process::id(), invocation_id = invocation.as_str(),
                    session_id = std::env::var("SANCTUARY_LOG_SESSION").ok().as_deref(); "command started");
                let previous = std::panic::take_hook();
                std::panic::set_hook(Box::new(move |info| {
                    log::error!(target: FILE_TARGET, event = "panic"; "{info}");
                    previous(info);
                }));
            }
            Some(handle)
        }
        Err(error) => {
            warn(command, error);
            None
        }
    };
    Session {
        handle,
        file_level,
        started,
    }
}

// A backend filter routes ordinary events to its built-in file writer; the
// primary destination handles optional stderr. No custom writer or formatter.
struct FileRouting(LevelFilter);
impl LogLineFilter for FileRouting {
    fn write(
        &self,
        now: &mut DeferredNow,
        record: &Record<'_>,
        console: &dyn LogLineWriter,
    ) -> io::Result<()> {
        if record.level() <= self.0 {
            log::logger().log(&record.to_builder().target(FILE_TARGET).build());
        }
        console.write(now, record)
    }
}

impl Session {
    /// Select optional console logging while preserving the file threshold.
    pub fn set_stderr_level(&self, level: LevelFilter) {
        if let Some(mut handle) = self.handle.clone() {
            let _ = handle.adapt_duplication_to_stderr(level.into());
            handle.set_new_spec(self.file_level.max(level).into());
        }
    }

    /// Parse arguments with Clap and retain its output and exit statuses.
    ///
    /// # Errors
    /// Returns the recorded status for help, version, or invalid arguments.
    pub fn parse<C: clap::Parser>(&self) -> Result<C, ExitCode> {
        C::try_parse().map_err(|error| self.clap_error(&error))
    }

    /// Report a custom parser's Clap error or help/version result.
    pub fn clap_error(&self, error: &clap::Error) -> ExitCode {
        if self.handle.is_some() && file_enabled() && error.use_stderr() {
            log::error!(target: FILE_TARGET, event = "argument_error"; "{error}");
        }
        let _ = error.print();
        self.complete(error.exit_code())
    }

    /// Flush logging and return the command's portable numeric exit status.
    pub fn finish(self, code: i32) -> ExitCode {
        self.complete(code)
    }

    fn complete(&self, code: i32) -> ExitCode {
        let code = u8::try_from(code).unwrap_or(1);
        if let Some(handle) = &self.handle {
            if file_enabled() {
                log::info!(target: FILE_TARGET, event = "command_finished", exit_code = code,
                    elapsed_ms = u64::try_from(self.started.elapsed().as_millis()).unwrap_or(u64::MAX); "command finished");
            }
            handle.flush();
        }
        ExitCode::from(code)
    }
}

fn file_enabled() -> bool {
    FILE_ENABLED.get().copied().unwrap_or(false)
}

/// Record an already-reported error to the file destination without echoing it.
pub fn record_error(message: impl Display) {
    if file_enabled() {
        log::error!(target: FILE_TARGET, event = "error"; "{message}");
    }
}

/// Persist an error and print the same plain line, even when logging is off.
#[macro_export]
macro_rules! diagnostic {
    ($($arg:tt)*) => {{ $crate::report(format_args!($($arg)*)); }};
}

/// Implements [`diagnostic!`] with the command's original terminal diagnostic.
pub fn report(message: fmt::Arguments<'_>) {
    record_error(message);
    let _ = writeln!(io::stderr().lock(), "{message}");
}

fn warn(command: &str, message: impl Display) {
    let _ = writeln!(io::stderr().lock(), "{command}: logging warning: {message}");
}
