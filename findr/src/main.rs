use clap::Parser;
use std::io::{BufWriter, Write};
use walkdir::WalkDir;

mod cli;
use crate::cli::{Cli, CliError};
use findr::{is_name_matches, is_type_matches};

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
                    let type_match = is_type_matches(&entry, &cli.entry_types);
                    let name_match = is_name_matches(&entry, &cli.names);

                    if type_match && name_match {
                        writeln!(writer, "{}", entry.path().display())?;
                    }
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
