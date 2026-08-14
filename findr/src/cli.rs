use clap::{ArgAction, Parser, ValueEnum, builder::PossibleValue};
use regex::Regex;
use std::path::PathBuf;
use thiserror::Error;

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

#[derive(Debug, PartialEq, Eq, Clone)]
pub enum EntryType {
    Dir,
    File,
    Link,
}

impl ValueEnum for EntryType {
    fn value_variants<'a>() -> &'a [Self] {
        &[Self::Dir, Self::File, Self::Link]
    }

    fn to_possible_value(&self) -> Option<clap::builder::PossibleValue> {
        Some(match self {
            Self::Dir => PossibleValue::new("d"),
            Self::File => PossibleValue::new("f"),
            Self::Link => PossibleValue::new("l"),
        })
    }
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
