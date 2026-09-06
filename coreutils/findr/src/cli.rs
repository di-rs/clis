use clap::{ArgAction, Parser};
use regex::Regex;
use std::path::PathBuf;
use thiserror::Error;

use findr::EntryType;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal find implementation
pub struct Cli {
    /// Search paths
    #[arg(value_name = "PATHS", default_value = ".")]
    pub paths: Vec<PathBuf>,
    /// Names pattern
    #[arg(value_name = "NAME", short('n'), long("name"), value_parser(Regex::new), action(ArgAction::Append), num_args(0..))]
    pub names: Vec<Regex>,
    /// Entry types
    #[arg(value_name = "TYPE", short('t'), long("type"), value_parser(clap::value_parser!(EntryType)), action(ArgAction::Append), num_args(0..))]
    pub entry_types: Vec<EntryType>,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
