use clap::{CommandFactory, Parser};
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
    path::Path,
};

mod cli;
use crate::cli::{Cli, CliError};

fn main() {
    let cli = Cli::parse();
    match run(cli) {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(CliError::Config) => {
            std::process::exit(exitcode::CONFIG);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: Cli) -> Result<(), CliError> {
    let mut writer = get_writer();
    let delimiter = cli.parse_delimeter()?;

    Ok(())
}

fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            let _ = Cli::command().print_help();
            return Err(CliError::Config);
        }
        Ok(Box::new(BufReader::new(stdin().lock())))
    } else {
        Ok(Box::new(BufReader::new(File::open(path)?)))
    }
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
