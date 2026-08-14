use std::path::PathBuf;
use clap::Parser;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `cut` implementation
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILES", default_value = "-")]
    pub files: Vec<PathBuf>,

    /// Field delimiter. Must be a single byte character
    #[arg(short('d'), long, value_name = "DELIMETER", default_value = "\t")]
    pub delimiter: String,

    #[command(flatten)]
    pub extract: ArgsExtract,
}

impl Cli {
    pub fn parse_delimeter(&self) -> Result<u8, CliError> {
        let delim_bytes = self.delimiter.as_bytes();
        if delim_bytes.len() != 1 {
            return Err(CliError::Config);
        }
         #[allow(clippy::indexing_slicing)]
        let delimeter = delim_bytes[0];
        Ok(delimeter)
    }
}

#[derive(Debug, clap::Args)]
#[group(required = true, multiple = false)]
pub struct ArgsExtract {
    /// Selected fields
    #[arg(short, long, value_name = "FIELDS")]
    pub fields: Option<String>,

    /// Selected bytes
    #[arg(short, long, value_name = "BYTES")]
    pub bytes: Option<String>,

    /// Selected chars
    #[arg(short, long, value_name = "CHARS")]
    pub chars: Option<String>,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}