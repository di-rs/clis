use std::{ffi::OsString, path::PathBuf, str::FromStr};
use tracing::level_filters::LevelFilter;

/// Diagnostic event and timing encoding.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum Format {
    #[default]
    Text,
    Json,
}

impl FromStr for Format {
    type Err = &'static str;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "text" => Ok(Self::Text),
            "json" => Ok(Self::Json),
            _ => Err("expected text or json"),
        }
    }
}

/// A diagnostic stream, separate from the command's data output.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub enum Destination {
    #[default]
    Stderr,
    /// Created exclusively; an existing file or symlink is an error.
    File(PathBuf),
}

/// Explicit adapter settings. An absent field permits environment/default fallback.
#[derive(Clone, Debug, Default)]
pub struct Overrides {
    pub level: Option<LevelFilter>,
    pub format: Option<Format>,
    pub destination: Option<Destination>,
    pub timings: Option<bool>,
}

/// Fully resolved settings; constructing this type has no process side effects.
#[derive(Clone, Debug)]
pub struct Config {
    pub level: LevelFilter,
    pub format: Format,
    pub destination: Destination,
    pub timings: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            level: LevelFilter::OFF,
            format: Format::Text,
            destination: Destination::Stderr,
            timings: false,
        }
    }
}

/// An invalid effective environment setting. Values are omitted from diagnostics.
#[derive(Debug, thiserror::Error)]
#[error("invalid {variable}: {expected}")]
pub struct ConfigError {
    pub variable: &'static str,
    pub expected: &'static str,
}

impl Config {
    /// Resolve each field from explicit overrides, supplied environment, then defaults.
    /// The lookup is injected; this function never reads or changes process state.
    ///
    /// # Errors
    /// Returns an error for invalid effective environment settings or empty file paths.
    pub fn resolve(
        overrides: Overrides,
        lookup: impl Fn(&str) -> Option<OsString>,
    ) -> Result<Self, ConfigError> {
        let level = resolve(
            overrides.level,
            &lookup,
            "CLIS_LOG_LEVEL",
            LevelFilter::OFF,
            "expected off, error, warn, info, debug or trace",
            |value| parse_level(value).ok(),
        )?;
        let format = resolve(
            overrides.format,
            &lookup,
            "CLIS_LOG_FORMAT",
            Format::Text,
            "expected text or json",
            |value| value.parse().ok(),
        )?;
        let timings = resolve(
            overrides.timings,
            &lookup,
            "CLIS_TIMINGS",
            false,
            "expected true or false",
            |value| value.parse().ok(),
        )?;
        let destination = overrides.destination.unwrap_or_else(|| {
            lookup("CLIS_LOG_FILE").map_or(Destination::Stderr, |path| {
                if path == "-" {
                    Destination::Stderr
                } else {
                    Destination::File(path.into())
                }
            })
        });
        if matches!(&destination, Destination::File(path) if path.as_os_str().is_empty()) {
            return Err(ConfigError {
                variable: "CLIS_LOG_FILE",
                expected: "expected a path or - for stderr",
            });
        }
        Ok(Self {
            level,
            format,
            destination,
            timings,
        })
    }
}

fn resolve<T>(
    explicit: Option<T>,
    lookup: &impl Fn(&str) -> Option<OsString>,
    variable: &'static str,
    default: T,
    expected: &'static str,
    parse: impl FnOnce(&str) -> Option<T>,
) -> Result<T, ConfigError> {
    if let Some(value) = explicit {
        return Ok(value);
    }
    let Some(value) = lookup(variable) else {
        return Ok(default);
    };
    value
        .to_str()
        .filter(|value| !value.is_empty())
        .and_then(parse)
        .ok_or(ConfigError { variable, expected })
}

/// Parse the shared level vocabulary for adapters without accepting numeric aliases.
///
/// # Errors
/// Rejects anything other than off, error, warn, info, debug or trace.
pub fn parse_level(value: &str) -> Result<LevelFilter, &'static str> {
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
