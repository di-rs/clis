use clap::Parser;
use regex::{Regex, RegexBuilder};
use std::path::PathBuf;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `fortune` implementation
pub struct Cli {
    /// Source input file(s) or directories
    #[arg(value_name = "FILES", required(true))]
    pub files: Vec<PathBuf>,

    /// Pattern
    #[arg(short('m'), long, value_name = "PATTERN")]
    pub pattern: Option<String>,

    /// Case-insensitive pattern matching
    #[arg(short('i'), long)]
    pub insensitive: bool,

    /// Random seed
    #[arg(short('s'), long, value_name = "SEED", value_parser(clap::value_parser!(u64)))]
    pub seed: Option<u64>,
}

impl Cli {
    pub fn try_parse_pattern(&self) -> Result<Option<Regex>, CliError> {
        self.pattern
            .as_ref()
            .map(|pattern| {
                RegexBuilder::new(pattern)
                    .case_insensitive(self.insensitive)
                    .build()
                    .map_err(|_| CliError::InvalidPattern(pattern.clone()))
            })
            .transpose()
    }
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error("invalid pattern passed `{0}`")]
    InvalidPattern(String),
    #[error("{0}: cannot open file {1}")]
    FileOpen(PathBuf, std::io::Error),
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
