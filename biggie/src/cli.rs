use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Generate big text files
pub struct Cli {
    /// Output filename
    #[arg(value_name = "FILE", default_value = "out.txt")]
    pub file: PathBuf,

    /// Number of lines
    #[arg(
        short('n'),
        long,
        default_value = "100000",
        value_name = "LINES",
        value_parser = clap::value_parser!(u64).range(1..)
    )]
    pub lines: u64,

    #[command(flatten)]
    pub verbosity: clap_verbosity_flag::Verbosity,
}
