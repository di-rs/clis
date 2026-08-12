use clap::Parser;
use std::io::Write;
use thiserror::Error;

use wcr::FileInfo;

#[allow(clippy::struct_excessive_bools)]
#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal wc implementation
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILE", default_value = "-")]
    pub files: Vec<String>,

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

impl Cli {
    pub fn normalize(&mut self) {
        if [self.lines, self.words, self.chars, self.bytes]
            .iter()
            .all(|v| !v)
        {
            self.lines = true;
            self.bytes = true;
            self.words = true;
        }
    }

    pub fn write_info_line(
        &self,
        mut writer: impl Write,
        info: &FileInfo,
        name: &str,
    ) -> Result<(), CliError> {
        if self.lines {
            write!(writer, "{:>8}", info.num_lines)?;
        }
        if self.words {
            write!(writer, "{:>8}", info.num_words)?;
        }
        if self.chars {
            write!(writer, "{:>8}", info.num_chars)?;
        }
        if self.bytes {
            write!(writer, "{:>8}", info.num_bytes)?;
        }
        if name != "-" {
            write!(writer, " {name}")?;
        }
        writeln!(writer)?;
        Ok(())
    }
}
