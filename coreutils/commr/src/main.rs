use clap::{CommandFactory, Parser};
use std::{
    fs::File,
    io::{BufRead, BufReader, IsTerminal, stdin},
    path::Path,
};

mod cli;
use crate::cli::{Cli, CliError};
use commr::get_lines;

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
    let file1 = &cli.file1;
    let file2 = &cli.file2;
    if file1 == Path::new("-") && file2 == Path::new("-") {
        return Err(CliError::BothFilesStdin);
    }

    let reader1 = get_reader(file1)?;
    let reader2 = get_reader(file2)?;

    get_lines(reader1, reader2, cli.insensitive, cli);

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
