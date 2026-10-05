//! CLI-owned argument mapping; the domain and cli-tracing have no Clap dependency.

use clap::{Args, Parser};
use cli_tracing::{Config, Destination, Format, Overrides, TracingSession};
use std::path::PathBuf;
use tracing::level_filters::LevelFilter;

#[derive(Parser)]
struct Cli {
    #[command(flatten)]
    diagnostics: Diagnostics,
}

#[derive(Args)]
struct Diagnostics {
    /// Severity threshold: off, error, warn, info, debug, trace
    #[arg(long, value_parser = cli_tracing::parse_level)]
    log_level: Option<LevelFilter>,
    /// Diagnostic encoding: text or json
    #[arg(long)]
    log_format: Option<Format>,
    /// Create a new file, or use - for stderr
    #[arg(long)]
    log_file: Option<PathBuf>,
    /// Collect stage timings independently of logging
    #[arg(long, num_args = 0..=1, default_missing_value = "true", require_equals = true)]
    timings: Option<bool>,
}

impl From<Diagnostics> for Overrides {
    fn from(args: Diagnostics) -> Self {
        Self {
            level: args.log_level,
            format: args.log_format,
            destination: args.log_file.map(|path| {
                if path.as_os_str() == "-" {
                    Destination::Stderr
                } else {
                    Destination::File(path)
                }
            }),
            timings: args.timings,
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    let config = Config::resolve(cli.diagnostics.into(), |key| std::env::var_os(key))?;
    let session = TracingSession::new(&config)?;
    tracing::dispatcher::with_default(session.dispatch(), || {
        let stage = tracing::info_span!(target: "clis::timing", "example", items = 1);
        let _entered = stage.enter();
        tracing::info!("example completed");
    });
    session.finish()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    #![allow(
        clippy::panic_in_result_fn,
        reason = "Test assertions fail the test; Result propagates parse errors."
    )]
    use super::*;

    #[test]
    fn absent_cli_values_allow_environment_fallback() -> Result<(), Box<dyn std::error::Error>> {
        let cli = Cli::try_parse_from(["example"])?;
        let config = Config::resolve(cli.diagnostics.into(), |key| match key {
            "CLIS_LOG_LEVEL" => Some("debug".into()),
            "CLIS_TIMINGS" => Some("true".into()),
            _ => None,
        })?;
        assert_eq!(config.level, LevelFilter::DEBUG);
        assert!(config.timings);
        Ok(())
    }

    #[test]
    fn explicit_off_false_and_stderr_override_environment() -> Result<(), Box<dyn std::error::Error>>
    {
        let cli = Cli::try_parse_from([
            "example",
            "--log-level=off",
            "--log-format=json",
            "--log-file=-",
            "--timings=false",
        ])?;
        let config = Config::resolve(cli.diagnostics.into(), |_| Some("invalid".into()))?;
        assert_eq!(config.level, LevelFilter::OFF);
        assert_eq!(config.format, Format::Json);
        assert_eq!(config.destination, Destination::Stderr);
        assert!(!config.timings);
        Ok(())
    }

    #[test]
    fn bare_timings_enables_and_invalid_values_fail() -> Result<(), clap::Error> {
        let cli = Cli::try_parse_from(["example", "--timings"])?;
        assert_eq!(cli.diagnostics.timings, Some(true));
        for option in ["--log-level=3", "--log-format=yaml", "--timings=perhaps"] {
            assert!(Cli::try_parse_from(["example", option]).is_err());
        }
        Ok(())
    }
}
