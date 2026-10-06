use clap::{CommandFactory, Parser};

#[derive(Debug, Parser)]
#[command(version, about, long_about = env!("CARGO_PKG_DESCRIPTION"))]
pub struct Cli {
    #[command(flatten)]
    pub logging: cli_tracing::GlobalLogArgs,
}

impl Cli {
    pub fn try_parse_validated_from(
        args: impl IntoIterator<Item = impl Into<std::ffi::OsString> + Clone>,
    ) -> Result<cli_tracing::LogArgs, clap::Error> {
        let cli = Self::try_parse_from(args)?;
        cli.logging
            .resolve()
            .map_err(|error| Self::command().error(clap::error::ErrorKind::ValueValidation, error))
    }
}

#[cfg(test)]
mod tests {
    use super::Cli;

    #[test]
    fn rejects_invalid_effective_logging_before_initialization() {
        let error = Cli::try_parse_validated_from(["cli-bench", "--log-level", "invalid"])
            .err()
            .map(|error| error.kind());
        assert_eq!(error, Some(clap::error::ErrorKind::ValueValidation));
    }

    #[test]
    fn help_and_version_bypass_logging_validation() {
        for (flag, expected) in [
            ("--help", clap::error::ErrorKind::DisplayHelp),
            ("--version", clap::error::ErrorKind::DisplayVersion),
        ] {
            assert_eq!(
                Cli::try_parse_validated_from(["cli-bench", flag, "-L", "invalid"])
                    .err()
                    .map(|error| error.kind()),
                Some(expected)
            );
        }
    }
}
