use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Generate big text files
pub struct Cli {
    /// Output filename
    #[arg(value_name = "FILE", default_value = "out.txt")]
    pub file: PathBuf,

    /// Number of lines
    #[arg(
        short('n'),
        long,
        default_value = "100000",
        value_name = "LINES",
        value_parser = clap::value_parser!(u64).range(1..)
    )]
    pub lines: u64,

    /// Diagnostic severity threshold (overrides legacy verbosity aliases)
    #[arg(long, value_parser = cli_tracing::parse_level)]
    pub log_level: Option<tracing::level_filters::LevelFilter>,

    /// Diagnostic encoding: text or json
    #[arg(long)]
    pub log_format: Option<cli_tracing::Format>,

    /// Create a new diagnostic file, or use - for stderr
    #[arg(long)]
    pub log_file: Option<PathBuf>,

    /// Collect stage busy/idle times independently of logging
    #[arg(long, num_args = 0..=1, default_missing_value = "true", require_equals = true)]
    pub timings: Option<bool>,

    #[command(flatten)]
    pub verbosity: clap_verbosity_flag::Verbosity,
}

impl Cli {
    pub fn diagnostic_overrides(&self) -> cli_tracing::Overrides {
        cli_tracing::Overrides {
            level: self.log_level.or_else(|| {
                self.verbosity
                    .is_present()
                    .then(|| self.verbosity.tracing_level_filter())
            }),
            format: self.log_format,
            destination: self.log_file.as_ref().map(|path| {
                if path.as_os_str() == "-" {
                    cli_tracing::Destination::Stderr
                } else {
                    cli_tracing::Destination::File(path.clone())
                }
            }),
            timings: self.timings,
        }
    }
}
