use clap::Parser;
use std::{path::PathBuf, str::FromStr};
use thiserror::Error;

use tailr::TakeValue;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `tail` implementation
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILES", required(true))]
    pub files: Vec<PathBuf>,

    /// Number of lines
    #[arg(
        short('n'),
        long,
        value_name = "LINES",
        default_value = "10",
        value_parser(TakeValue::from_str)
    )]
    pub lines: TakeValue,

    /// Number of bytes
    #[arg(
        short('c'),
        long,
        value_name = "BYTES",
        conflicts_with("lines"),
        value_parser(TakeValue::from_str)
    )]
    pub bytes: Option<TakeValue>,

    /// Supress header printing when multiple files provided
    #[arg(short('q'), long)]
    pub quiet: bool,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("{0}: cannot open file {1}")]
    FileOpen(PathBuf, std::io::Error),
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
