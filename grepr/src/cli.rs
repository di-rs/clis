use clap::Parser;
use regex::{Regex, RegexBuilder};
use std::path::PathBuf;
use thiserror::Error;

mod verbosity;
mod writer;

pub use writer::get_writer;

#[allow(clippy::struct_excessive_bools)]
#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `grep` implementation
pub struct Cli {
    /// The pattern to look for
    #[arg(value_name = "PATTERN", required(true))]
    pub pattern: String,

    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILES", default_value = "-")]
    pub files: Vec<PathBuf>,

    /// Case-insensitive
    #[arg(short('i'), long)]
    pub insensitive: bool,

    /// Recursive search
    #[arg(short('r'), long)]
    pub recursive: bool,

    /// Count occurrences
    #[arg(short('c'), long)]
    pub count: bool,

    /// Invert match
    #[arg(short('v'), long("invert-match"))]
    pub invert: bool,

    /// Verbosity flag for debugging and full app logs
    #[command(flatten)]
    pub verbosity: verbosity::Verbosity,
}

impl Cli {
    pub fn try_parse_pattern(&self) -> Result<Regex, CliError> {
        RegexBuilder::new(&self.pattern)
            .case_insensitive(self.insensitive)
            .build()
            .map_err(|_| CliError::InvalidPattern(self.pattern.clone()))
    }
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error("invalid pattern passed `{0}`")]
    InvalidPattern(String),
    #[error(transparent)]
    IO(#[from] std::io::Error),
    #[error(transparent)]
    ParseError(#[from] grepr::ParseError),
}
