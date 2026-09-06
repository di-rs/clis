use clap::Parser;
use cutr::Extract;
use std::path::PathBuf;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `cut` implementation
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(value_name = "FILES", default_value = "-")]
    pub files: Vec<PathBuf>,

    /// Field delimiter. Must be a single byte character
    #[arg(
        short('d'),
        long,
        value_name = "DELIMETER",
        default_value = "\t",
        value_parser(Cli::parse_delimeter)
    )]
    pub delimiter: u8,

    #[command(flatten)]
    pub extract: ArgsExtract,
}

impl Cli {
    fn parse_delimeter(value: &str) -> Result<u8, CliError> {
        let delim_bytes = value.as_bytes();
        if delim_bytes.len() != 1 {
            return Err(CliError::BadDelimiter(value.to_owned()));
        }
        #[allow(clippy::indexing_slicing)]
        let delimeter = delim_bytes[0];
        Ok(delimeter)
    }
}

#[derive(Debug, clap::Args, Clone)]
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

impl TryFrom<&ArgsExtract> for Extract {
    type Error = CliError;

    #[allow(clippy::unreachable)]
    fn try_from(value: &ArgsExtract) -> Result<Self, Self::Error> {
        if let Some(fields) = &value.fields {
            Ok(Self::Fields(fields.parse()?))
        } else if let Some(bytes) = &value.bytes {
            Ok(Self::Bytes(bytes.parse()?))
        } else if let Some(chars) = &value.chars {
            Ok(Self::Chars(chars.parse()?))
        } else {
            unreachable!("Must have --fields, --bytes, or --chars");
        }
    }
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error("--delim `{0}` must be a single byte")]
    BadDelimiter(String),
    #[error(transparent)]
    IO(#[from] std::io::Error),
    #[error(transparent)]
    RangeParseParse(#[from] cutr::ParseError),
}
