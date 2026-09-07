use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust minimal `mkdir` implementation
pub struct Cli {
    /// Create nested directories if it was provided in the name
    #[arg(short = 'p', default_value_t = true)]
    pub parent: bool,

    /// The name(s) of the directory(ies) to create
    pub dirs: Vec<PathBuf>,
}
