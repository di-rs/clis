use clap::Parser;

#[derive(Parser, Debug)]
#[command(author, version, about)]
/// Rust `pwd` implementation
pub struct Cli {
    /// Physical path, resolves symlinks
    #[arg(short = 'P', conflicts_with("logical_path"))]
    pub physical_path: bool,

    /// Logical path, preserves symlinks
    #[arg(short = 'L')]
    pub logical_path: bool,
}
