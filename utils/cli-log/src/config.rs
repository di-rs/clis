use clap::Args;
use log::LevelFilter;
use std::{env, ffi::OsString, fs::DirBuilder, io, path::PathBuf};

/// Shared file logging arguments for a Clap parser.
#[derive(Args, Debug)]
pub struct LogArgs {
    /// File log threshold: off, error, warn, info, debug, trace
    #[arg(long, env = "SANCTUARY_LOG_LEVEL", default_value = "info", value_parser = parse_level)]
    log_level: LevelFilter,
}

impl LogArgs {
    /// Selected threshold for persistent logs.
    #[must_use]
    pub const fn level(&self) -> LevelFilter {
        self.log_level
    }
}

/// Global arguments resolved after Clap propagates subcommand flags.
#[derive(Args, Debug)]
pub struct GlobalLogArgs {
    /// File log threshold: off, error, warn, info, debug, trace
    #[arg(
        short = 'L',
        long,
        global = true,
        env = "SANCTUARY_LOG_LEVEL",
        default_value = "info"
    )]
    log_level: OsString,
}

impl GlobalLogArgs {
    /// Resolve the effective flag, allowing it to override the environment.
    ///
    /// # Errors
    /// Returns an error for unsupported or non-UTF-8 values.
    pub fn resolve(&self) -> Result<LogArgs, &'static str> {
        let value = self.log_level.to_str().ok_or("--log-level must be UTF-8")?;
        parse_level(value)
            .map(|log_level| LogArgs { log_level })
            .map_err(|_| "invalid --log-level: expected off, error, warn, info, debug or trace")
    }
}

pub fn parse_level(value: &str) -> Result<LevelFilter, &'static str> {
    match value {
        "off" => Ok(LevelFilter::Off),
        "error" => Ok(LevelFilter::Error),
        "warn" => Ok(LevelFilter::Warn),
        "info" => Ok(LevelFilter::Info),
        "debug" => Ok(LevelFilter::Debug),
        "trace" => Ok(LevelFilter::Trace),
        _ => Err("expected off, error, warn, info, debug or trace"),
    }
}

pub fn env_level(key: &str, default: LevelFilter) -> (LevelFilter, Option<String>) {
    match env::var(key) {
        Ok(value) => match parse_level(&value) {
            Ok(level) => (level, None),
            Err(error) => (default, Some(format!("invalid {key}: {error}"))),
        },
        Err(env::VarError::NotPresent) => (default, None),
        Err(env::VarError::NotUnicode(_)) => (default, Some(format!("{key} must be UTF-8"))),
    }
}

/// Resolve storage and privately create missing directories without changing existing modes.
pub fn log_dir() -> io::Result<PathBuf> {
    let root = nonempty_env("SANCTUARY_LOG_DIR")
        .or_else(default_log_dir)
        .ok_or_else(|| io::Error::other("no log directory is available"))?;
    let mut builder = DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(&root)?;
    Ok(root)
}

fn nonempty_env(key: &str) -> Option<PathBuf> {
    env::var_os(key)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

#[cfg(not(windows))]
fn default_log_dir() -> Option<PathBuf> {
    if let Some(path) = nonempty_env("XDG_STATE_HOME").filter(|path| path.is_absolute()) {
        return Some(path.join("sanctuary/logs"));
    }
    let home = nonempty_env("HOME").filter(|path| path.is_absolute())?;
    if cfg!(target_os = "macos") {
        Some(home.join("Library/Logs/sanctuary"))
    } else {
        Some(home.join(".local/state/sanctuary/logs"))
    }
}

#[cfg(windows)]
fn default_log_dir() -> Option<PathBuf> {
    nonempty_env("LOCALAPPDATA").map(|path| path.join("sanctuary/logs"))
}
