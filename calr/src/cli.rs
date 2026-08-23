use chrono::Datelike;
use clap::Parser;
use std::str::FromStr;
use thiserror::Error;

use calr::{Month, get_today};

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `cal` implementation
pub struct Cli {
    /// Year (1-9999)
    #[arg(value_parser(clap::value_parser!(i32).range(1..=9999)))]
    pub year: Option<i32>,

    /// Month name or number (1-12)
    #[arg(short, value_parser(Month::from_str))]
    pub month: Option<Month>,

    /// Show the whole current year
    #[arg(short('y'), long("year"), conflicts_with_all(["month", "year"]))]
    pub show_current_year: bool,
}

impl Cli {
    pub fn setup_default(&mut self) {
        let today = get_today();

        if self.month.is_none() && self.year.is_none() && !self.show_current_year {
            self.month = Some(Month::new(today.month()));
        }

        self.year = self.year.or_else(|| Some(today.year()));
    }
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
