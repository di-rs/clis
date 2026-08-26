use clap::Parser;
use std::io::{BufWriter, Write};

mod cli;
use crate::cli::{Cli, CliError};
use lsr::{find_files, get_formatted_output};

fn main() {
    let cli = Cli::parse();
    let result = run(&cli);

    match result {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let mut writer = get_writer();

    let paths = find_files(&cli.paths, cli.show_hidden)?;
    if cli.long {
        let output = get_formatted_output(&paths)?;
        writeln!(writer, "{output}")?;
    } else {
        for path in paths {
            writeln!(writer, "{}", path.display())?;
        }
    }

    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
