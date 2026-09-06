use clap::{CommandFactory, Parser};
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
};

mod cli;
use crate::cli::{Cli, CliError};
use uniqr::{UniqueList, report_unique_lines};

fn main() {
    let cli = Cli::parse();
    match run(cli) {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(CliError::Config) => {
            std::process::exit(exitcode::CONFIG);
        }
        Err(CliError::FileOpen { err, path }) => {
            eprint!("{path}: {err}");
            std::process::exit(exitcode::IOERR);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: Cli) -> Result<(), CliError> {
    let reader = get_reader(&cli.input_file)?;
    let writer = get_writer(cli.output_file)?;

    let list = UniqueList::from_reader(reader)?;
    report_unique_lines(writer, list, cli.count)?;

    Ok(())
}

fn get_reader(path: &str) -> Result<Box<dyn BufRead>, CliError> {
    if path == "-" {
        if stdin().is_terminal() {
            let _ = Cli::command().print_help();
            return Err(CliError::Config);
        }
        Ok(Box::new(BufReader::new(stdin().lock())))
    } else {
        let file = File::open(path).map_err(|err| CliError::FileOpen {
            err,
            path: path.to_string(),
        })?;
        Ok(Box::new(BufReader::new(file)))
    }
}

fn get_writer(path: Option<String>) -> Result<Box<dyn Write>, CliError> {
    if let Some(path) = path {
        let file = File::create(&path).map_err(|err| CliError::FileOpen { err, path })?;
        Ok(Box::new(BufWriter::new(file)))
    } else {
        let stdout = std::io::stdout();
        Ok(Box::new(BufWriter::new(stdout.lock())))
    }
}
