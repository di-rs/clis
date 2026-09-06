use clap::{CommandFactory, Parser};
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
};

mod cli;
use crate::cli::{Cli, CliError};
use wcr::{FileInfo, get_file_info};

fn main() {
    match run(Cli::parse()) {
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

fn run(mut cli: Cli) -> Result<(), CliError> {
    cli.normalize();
    let mut writer = get_writer();
    let mut total_info = FileInfo::default();

    for filename in &cli.files {
        match get_reader(filename) {
            Ok(reader) => {
                let info = get_file_info(reader)?;
                cli.write_info_line(&mut writer, &info, filename)?;
                total_info.add(&info);
            }
            Err(e) => eprintln!("{filename}: {e}"),
        }
    }

    if cli.files.len() > 1 {
        cli.write_info_line(&mut writer, &total_info, "total")?;
    }

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
        Ok(Box::new(BufReader::new(File::open(path)?)))
    }
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
