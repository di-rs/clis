use clap::Parser;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal uniq implementation
pub struct Cli {
    /// Input file, use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE", default_value = "-")]
    pub input_file: String,

    /// Output file
    #[arg(value_name = "OUTPUT FILE")]
    pub output_file: Option<String>,

    /// The count of the number of times the line occurred in input
    #[arg(short('c'), long)]
    pub count: bool,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error("failed to open the file")]
    FileOpen { err: std::io::Error, path: String },
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
