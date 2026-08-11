use clap::{CommandFactory, Parser};
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, stdin},
};

use crate::cli::{Args, CliError};
use catr::write_lines;

mod cli;

fn main() {
    match run(&Args::parse()) {
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

fn run(args: &Args) -> Result<(), CliError> {
    for filename in &args.files {
        let writer = BufWriter::new(std::io::stdout().lock());
        match open(filename) {
            Ok(reader) => {
                write_lines(reader, writer, &args.into())?;
            }
            Err(e) => eprintln!("Failed to open {filename}: {e}"),
        }
    }
    Ok(())
}

fn open(path: &str) -> Result<Box<dyn BufRead>, CliError> {
    match path {
        "-" => {
            if stdin().is_terminal() {
                let _ = Args::command().print_help();
                return Err(CliError::Config);
            }
            Ok(Box::new(BufReader::new(stdin().lock())))
        }
        path => Ok(Box::new(BufReader::new(File::open(path)?))),
    }
}
