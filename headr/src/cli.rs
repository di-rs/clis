use clap::Parser;
use thiserror::Error;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal head implementation
pub struct Args {}

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect config passed")]
    Config,
    #[error(transparent)]
    IO(#[from] std::io::Error),
}