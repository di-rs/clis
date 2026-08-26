use clap::Parser;
use std::{path::PathBuf};
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `ls` implementation
pub struct Cli {
    /// Files and/or directories
    #[arg(default_value = ".")]
    pub paths: Vec<PathBuf>,

    /// Long listing
    #[arg(short, long)]
    pub long: bool,

    /// Show all files
    #[arg(short('a'), long("all"))]
    pub show_hidden: bool,
}

#[derive(Error, Debug)]
pub enum CliError {
    #[error(transparent)]
    IO(#[from] std::io::Error),
}
