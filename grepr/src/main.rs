use clap::{CommandFactory, Parser};
use grepr::{find_files, find_matches};
use human_panic::setup_panic;
use log::info;
use std::io::{BufReader, IsTerminal};
use std::path::Path;
use std::{
    fs::File,
    io::{BufRead, BufWriter, Write, stdin},
};

mod cli;
use crate::cli::{Cli, CliError};

fn main() {
    setup_panic!();
    info!("starting up");

    let cli = Cli::parse();
    env_logger::Builder::new()
        .filter_level(cli.verbosity.log_level_filter())
        .init();

    let _ = ctrlc::set_handler(move || {
        println!("received Ctrl+C!");
    });

    let result = run(&cli);

    match result {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(CliError::Config) => {
            let _ = Cli::command().print_help();
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
    let pattern = cli.try_parse_pattern()?;

    let entries = find_files(&cli.files, cli.recursive);
    for entry in entries {
        let entry = entry?;
        match get_reader(&entry) {
            Ok(reader) => {
                find_matches(reader, &mut writer, &pattern, cli.invert)?;
            }
            Err(e) => eprintln!("Failed to open {}: {e}", entry.display()),
        }
    }
    Ok(())
}

fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            Err(CliError::Config)
        } else {
            Ok(Box::new(BufReader::new(stdin().lock())))
        }
    } else {
        Ok(Box::new(BufReader::new(File::open(path)?)))
    }
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
