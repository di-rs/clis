use clap::{CommandFactory, Parser};
use grepr::{count_matches, find_files, find_matches};
use human_panic::setup_panic;
use log::info;
use std::{fs, path::Path, io::Write};

mod cli;
use crate::cli::{Cli, CliError, get_writer, get_reader};

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
    let pattern = cli.try_parse_pattern()?;
    let mut writer = get_writer();

    let entries = find_files(&cli.files, cli.recursive);
    let multiple_files = entries.len() > 1;
    for entry in entries {
        let entry = entry?;
        match get_reader(&entry) {
            Ok(reader) => {
                let line_prefix = get_line_prefix(&entry, multiple_files)?;

                if cli.count {
                    write!(writer, "{line_prefix}")?;
                    count_matches(reader, &mut writer, &pattern, cli.invert)?;
                } else {
                    find_matches(reader, &mut writer, &pattern, cli.invert)?;
                }
            }
            Err(e) => eprintln!("Failed to open {}: {e}", entry.display()),
        }
    }
    Ok(())
}

fn get_line_prefix(path: &Path, multiple_files: bool) -> Result<String, std::io::Error> {
    if multiple_files {
        let full_path = fs::canonicalize(path)?;
        Ok(format!("{}:", full_path.display()))
    } else {
        Ok(String::new())
    }
}
