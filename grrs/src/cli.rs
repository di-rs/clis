use clap::Parser;

/// Search for a pattern  in a file and display the lines that contain it.
#[derive(Parser)]
#[command(arg_required_else_help = true)]
pub struct Cli {
    /// The pattern to look for
    pub pattern: String,
    /// The path to the file to read, use - to read from stdin (must not be a tty)
    pub path: std::path::PathBuf,
    /// Verbosity flag for debugging and full app logs
    #[command(flatten)]
    pub verbosity: clap_verbosity_flag::Verbosity,
}
