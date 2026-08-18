use clap::Parser;
use std::path::PathBuf;
use thiserror::Error;

#[allow(clippy::struct_excessive_bools)]
#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `comm` implementation
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE_1", default_value = "-")]
    pub file1: PathBuf,

    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE_2", default_value = "-")]
    pub file2: PathBuf,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}