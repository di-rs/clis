mod cli;
use clap::{CommandFactory, Parser};
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
    path::Path,
};

use crate::cli::{Cli, CliError};

fn main() {
    let cli = Cli::parse();
    let result = run(&cli);

    match result {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(CliError::Config) => {
            let _ = Cli::command().print_help();
            std::process::exit(exitcode::CONFIG);
        }
        Err(CliError::FileOpen(file_name, e)) => {
            eprintln!("{}: {e}", file_name.display());
            std::process::exit(exitcode::CONFIG);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let mut writer = get_writer();

    for (file_num, filename) in cli.files.iter().enumerate() {
        match get_reader(filename) {
            Ok(reader) => {}
            Err(e) => eprintln!("{}: {e}", filename.display()),
        }
    }
    Ok(())
}

fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            Err(CliError::Config)
        } else {
            Ok(Box::new(BufReader::new(stdin().lock())))
        }
    } else {
        let file = File::open(path).map_err(|e| CliError::FileOpen(path.to_owned(), e))?;
        Ok(Box::new(BufReader::new(file)))
    }
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
