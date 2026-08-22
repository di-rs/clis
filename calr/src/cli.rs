use clap::Parser;
use thiserror::Error;


#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `cal` implementation
pub struct Cli {
    /// Year (1-9999)
    #[arg(value_parser(clap::value_parser!(i32).range(1..=9999)))]
    pub year: Option<i32>,

    /// Month name or number (1-12)
    #[arg(short)]
    pub month: Option<String>,

    /// Show the whole current year
    #[arg(short('y'), long("year"), conflicts_with_all(["month", "year"]))]
    pub show_current_year: bool,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error(transparent)]
    IO(#[from] std::io::Error),
}