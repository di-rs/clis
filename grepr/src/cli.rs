use clap::Parser;
use regex::Regex;
use std::path::PathBuf;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `grep` implementation
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILES", default_value = "-")]
    pub files: Vec<PathBuf>,

    /// The pattern to look for
    #[arg(value_name = "PATTERN", value_parser(Regex::new))]
    pub pattern: Regex,
    
    /// Verbosity flag for debugging and full app logs
    #[command(flatten)]
    pub verbosity: clap_verbosity_flag::Verbosity,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}