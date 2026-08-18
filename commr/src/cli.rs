use clap::{ArgAction, Parser};
use std::path::PathBuf;
use thiserror::Error;

#[allow(clippy::struct_excessive_bools)]
#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `comm` implementation
pub struct Cli {
    /// First file to compare, use `-` to read from stdin 
    #[arg(value_name = "FILE_1", required(true))]
    pub file1: PathBuf,

    /// Second file to compare, use `-` to read from stdin 
    #[arg(value_name = "FILE_2", required(true))]
    pub file2: PathBuf,

    /// Supress printing of column 1
    #[arg(short('1'), action(ArgAction::SetTrue))]
    show_col1: bool,

    /// Supress printing of column 2
    #[arg(short('2'), action(ArgAction::SetTrue))]
    show_col2: bool,

    /// Supress printing of column 3
    #[arg(short('3'), action(ArgAction::SetTrue))]
    show_col3: bool,

    /// Case-insensitive comparison of lines
    #[arg(short('i'), long)]
    insensitive: bool,

    /// Output delimiter 
    #[arg(value_name = "DELIM", short('d'), long("output-delimiter"), default_value = "\t")]
    delimiter: String
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error("both input files cannot be STDIN (`-`)")]
    BothFilesStdin,
    #[error("cannot open provided file")]
    FileOpen(PathBuf, std::io::Error),
    #[error(transparent)]
    IO(#[from] std::io::Error),
}