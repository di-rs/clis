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
    let mut writer = get_writer(args.unbuffered);

    for filename in &args.files {
        match get_reader(filename) {
            Ok(reader) => {
                write_lines(reader, &mut writer, &args.into())?;
            }
            Err(e) => eprintln!("Failed to open {filename}: {e}"),
        }
    }
    Ok(())
}

fn get_reader(path: &str) -> Result<Box<dyn BufRead>, CliError> {
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

fn get_writer(unbuffered: bool) -> Box<dyn std::io::Write> {
    let stdout = std::io::stdout();
    if unbuffered {
        Box::new(BufWriter::new(stdout.lock()))
    } else {
        Box::new(stdout.lock())
    }
}
