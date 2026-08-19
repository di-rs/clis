mod cli;
use clap::Parser;
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, Seek, Write},
    path::Path,
};

use crate::cli::{Cli, CliError};
use tailr::{get_buffer_info, print_bytes, print_lines};

fn main() {
    let cli = Cli::parse();
    let result = run(&cli);

    match result {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(CliError::FileOpen(_, e)) => {
            eprintln!("{e}");
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
    let num_files = cli.files.len();

    for (file_num, filename) in cli.files.iter().enumerate() {
        match get_reader(filename) {
            Ok(reader) => {
                if !cli.quiet && num_files > 1 {
                    let filename = filename.display();
                    let delim = if file_num > 0 { "\n" } else { "" };
                    writeln!(writer, "{delim}==> {filename} <==")?;
                }

                let total_info = get_buffer_info(get_reader(filename)?)?;

                if let Some(num_bytes) = &cli.bytes {
                    print_bytes(reader, &mut writer, *num_bytes, total_info.bytes)?;
                } else {
                    print_lines(reader, &mut writer, cli.lines, total_info.lines)?;
                }
            }
            Err(e) => eprintln!("{}: {e}", filename.display()),
        }
    }
    Ok(())
}

fn get_reader(path: &Path) -> Result<impl BufRead + Seek, CliError> {
    let file = File::open(path).map_err(|e| CliError::FileOpen(path.to_owned(), e))?;
    Ok(BufReader::new(file))
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
