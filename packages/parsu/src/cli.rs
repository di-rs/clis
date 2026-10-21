use clap::Parser;

#[derive(Debug, Parser)]
#[command(author, version, about)]
/// Parsing cli for the simple XML
pub struct Cli {
    /// Input file(s), use `-` to read from stdin (must not be a tty)
    #[arg(required(true))]
    pub file: String,
    /// Emit a stable JSON tree or error object
    #[arg(long, global = true)]
    pub json: bool,
}
