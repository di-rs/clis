use clap::Parser;
use std::{
    fs::File,
    io::{BufReader, BufWriter, Read, Seek, Write},
    path::Path,
};

mod cli;
use crate::cli::{Cli, CliError};
use tailr::{get_total_lines, print_bytes, print_lines};

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
        match get_file_reader(filename) {
            Ok(file) => {
                if !cli.quiet && num_files > 1 {
                    let filename = filename.display();
                    let delim = if file_num > 0 { "\n" } else { "" };
                    writeln!(writer, "{delim}==> {filename} <==")?;
                }

                if let Some(num_bytes) = &cli.bytes {
                    let metadata = std::fs::metadata(filename)?;
                    let total_bytes = metadata.len();
                    print_bytes(file, &mut writer, *num_bytes, total_bytes)?;
                } else {
                    let total_lines = get_file_lines_count(filename)?;
                    let reader = BufReader::new(file);
                    print_lines(reader, &mut writer, cli.lines, total_lines)?;
                }
            }
            Err(e) => eprintln!("{}: {e}", filename.display()),
        }
    }
    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}

fn get_file_reader(path: &Path) -> Result<impl Read + Seek, CliError> {
    let file = File::open(path).map_err(|e| CliError::FileOpen(path.to_owned(), e))?;
    Ok(file)
}

fn get_file_lines_count(path: &Path) -> Result<u64, CliError> {
    let file = get_file_reader(path)?;
    let lines_count = get_total_lines(file)?;
    Ok(lines_count)
}
