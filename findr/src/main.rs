use clap::Parser;
use std::io::{BufWriter, Write};
use walkdir::WalkDir;

mod cli;
use crate::cli::{Cli, CliError};

fn main() {
    let cli = Cli::parse();
    match run(cli) {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: Cli) -> Result<(), CliError> {
    let mut writer = get_writer();

    for path in cli.paths {
        for entry in WalkDir::new(path) {
            match entry {
                Ok(entry) => {
                    write!(writer, "{}", entry.path().display())?;
                }
                Err(e) => {
                    eprintln!("{e}");
                }
            }
        }
    }

    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
