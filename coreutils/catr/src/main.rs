use clap::CommandFactory;
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
};

use crate::cli::{Args, CliError};
use catr::write_lines;

mod cli;

fn main() -> std::process::ExitCode {
    let logging = cli_log::init("catr");
    let cli = match logging.parse::<Args>() {
        Ok(cli) => cli,
        Err(status) => return status,
    };

    let status = match run(&cli) {
        Ok(()) => exitcode::OK,
        Err(CliError::Config) => {
            cli_log::record_error(CliError::Config);
            exitcode::CONFIG
        }
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    };
    logging.finish(status)
}

fn run(args: &Args) -> Result<(), CliError> {
    let mut writer = get_writer(args.unbuffered);

    for filename in &args.files {
        match get_reader(filename) {
            Ok(reader) => {
                write_lines(reader, &mut writer, &args.into())?;
            }
            Err(e) => cli_log::diagnostic!("Failed to open {filename}: {e}"),
        }
    }
    writer.flush()?;
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
