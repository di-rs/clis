use std::path::PathBuf;
use clap::Parser;
use thiserror::Error;

#[allow(clippy::struct_excessive_bools)]
#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal head implementation
pub struct Args {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE", default_value = "-")]
    pub files: Vec<PathBuf>,

    /// Show count of lines
    #[arg(short('l'), long, value_name = "LINES")]
    pub lines: bool,

    /// Show count of words
    #[arg(short('w'), long, value_name = "WORDS")]
    pub words: bool,

    /// Show count of chars
    #[arg(short('m'), long, value_name = "CHARS")]
    pub chars: bool,

    /// Show count of bytes
    #[arg(short('c'), long, value_name = "BYTES", conflicts_with("chars"))]
    pub bytes: bool,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}