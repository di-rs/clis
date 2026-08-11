use clap::Parser;
use thiserror::Error;

use catr::Flags;

#[derive(Debug, Parser)]
#[command(author, version, about)]
/// Rust minimal version of `cat`
pub struct Args {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE", default_value = "-")]
    pub files: Vec<String>,

    /// Number lines
    #[arg(short('n'), long("number"), conflicts_with("number_nonblank_lines"))]
    pub number_lines: bool,

    /// Number non-blank lines
    #[arg(short('b'), long("number-nonblank"))]
    pub number_nonblank_lines: bool,
}

impl From<&Args> for Flags {
    fn from(value: &Args) -> Self {
        Self {
            number_lines: value.number_lines,
            number_nonblank_lines: value.number_nonblank_lines,
        }
    }
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}