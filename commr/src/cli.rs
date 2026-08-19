use clap::Parser;
use std::{io::Write, path::PathBuf};
use thiserror::Error;

use commr::Column;

#[allow(clippy::struct_excessive_bools)]
#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `comm` implementation
/// Input file lines should be sorted
pub struct Cli {
    /// First file to compare, use `-` to read from stdin
    #[arg(value_name = "FILE_1", required(true))]
    pub file1: PathBuf,

    /// Second file to compare, use `-` to read from stdin
    #[arg(value_name = "FILE_2", required(true))]
    pub file2: PathBuf,

    /// Supress printing of column 1
    #[arg(short('1'), default_value = "true")]
    show_col1: bool,

    /// Supress printing of column 2
    #[arg(short('2'), default_value = "true")]
    show_col2: bool,

    /// Supress printing of column 3
    #[arg(short('3'), default_value = "true")]
    show_col3: bool,

    /// Case-insensitive comparison of lines
    #[arg(short('i'), long)]
    pub insensitive: bool,

    /// Output delimiter
    #[arg(
        value_name = "DELIM",
        short('d'),
        long("output-delimiter"),
        default_value = "\t"
    )]
    delimiter: String,
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

impl Cli {
    pub fn print_column(&self, mut writer: impl Write, col: Column) -> Result<(), std::io::Error> {
        let mut columns = vec![];

        match col {
            Column::Col1(val) => {
                if self.show_col1 {
                    columns.push(val);
                }
            }
            Column::Col2(val) => {
                if self.show_col2 {
                    if self.show_col1 {
                        columns.push("");
                    }
                    columns.push(val);
                }
            }
            Column::Col3(val) => {
                if self.show_col3 {
                    if self.show_col1 {
                        columns.push("");
                    }
                    if self.show_col2 {
                        columns.push("");
                    }
                    columns.push(val);
                }
            }
        }

        if !columns.is_empty() {
            writeln!(writer, "{}", columns.join(&self.delimiter))?;
        }

        Ok(())
    }
}
