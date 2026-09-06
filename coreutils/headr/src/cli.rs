use clap::Parser;
use std::path::PathBuf;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal head implementation
pub struct Args {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE", default_value = "-")]
    pub files: Vec<PathBuf>,

    /// Number of lines
    #[arg(short('n'), long, value_name = "LINES", default_value = "10", value_parser = clap::value_parser!(u64).range(1..))]
    pub lines: u64,

    /// Number of bytes
    #[arg(short('c'), long, value_name = "BYTES", conflicts_with("lines"), value_parser = clap::value_parser!(u64).range(1..))]
    pub bytes: Option<u64>,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
