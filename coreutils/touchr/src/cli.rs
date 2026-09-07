use chrono::NaiveDateTime;
use clap::Parser;
use color_eyre::eyre::eyre;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `touch` implementation
/// Make file(s) in the current directory. If file exists change the timestamp
pub struct Cli {
    /// Skip file creation if it does not exist
    #[arg(short = 'c')]
    pub skip_create: bool,

    #[allow(clippy::doc_markdown)]
    /// Set custom timestamp in the format [[CC]YY]MMDDhhmm[.ss]
    #[arg(short = 't', value_parser(Cli::parse_custom_timestamp))]
    pub timestamp: Option<NaiveDateTime>,

    /// The name(s) of the file(s) to create
    pub files: Vec<PathBuf>,
}

impl Cli {
    fn parse_custom_timestamp(timestamp: &str) -> color_eyre::Result<NaiveDateTime> {
        let format = "%Y%m%d%H%M%S";
        let full_timestamp = if timestamp.len() == 12 {
            format!("{timestamp}00") // Add seconds if not provided
        } else {
            timestamp.to_string()
        };

        NaiveDateTime::parse_from_str(&full_timestamp, format)
            .map_err(|_| eyre!("Invalid timestamp format. Use [[CC]YY]MMDDhhmm[.ss]"))
    }
}
