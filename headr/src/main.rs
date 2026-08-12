use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
    path::Path,
};

use clap::{CommandFactory, Parser};
use headr::{write_bytes, write_file_header, write_lines};

mod cli;
use crate::cli::{Args, CliError};

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
    let mut writer = get_writer();
    let files_count = args.files.len();

    for (file_num, filename) in args.files.iter().enumerate() {
        match get_reader(filename) {
            Ok(reader) => {
                if files_count > 1 {
                    if file_num > 0 {
                        writeln!(writer)?;
                    }
                    write_file_header(&mut writer, filename)?;
                }

                if let Some(bytes) = args.bytes {
                    write_bytes(reader, &mut writer, bytes)?;
                } else {
                    write_lines(reader, &mut writer, args.lines)?;
                }
            }
            Err(e) => eprintln!("{}: {e}", filename.display()),
        }
    }
    Ok(())
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
