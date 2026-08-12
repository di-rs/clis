use clap::{CommandFactory, Parser};
use wcr::write_file_info;
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
    path::Path,
};

mod cli;
use crate::cli::{Args, CliError};

fn main() {
    match run(Args::parse()) {
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

fn run(mut args: Args) -> Result<(), CliError> {
    normalize_args(&mut args);
    let mut writer = get_writer();

    for filename in &args.files {
        match get_reader(filename) {
            Ok(reader) => {
                write_file_info(reader, &mut writer)?;
            }
            Err(e) => eprintln!("{}: {e}", filename.display()),
        }
    }
    Ok(())
}

fn normalize_args(args: &mut Args) {
    if [args.lines, args.words, args.chars, args.bytes]
        .iter()
        .all(|v| !v)
    {
        args.lines = true;
        args.bytes = true;
        args.words = true;
    }
}

fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            let _ = Args::command().print_help();
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
