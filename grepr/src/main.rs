use clap::{CommandFactory, Parser};
use grepr::{count_matches, find_files, find_matches};
use human_panic::setup_panic;
use log::info;

mod cli;
use crate::cli::{Cli, CliError, get_reader, get_writer};

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

    let entries = find_files(&cli.files, cli.recursive);
    let multiple_files = entries.len() > 1;

    for entry in entries {
        match entry {
            Ok(entry) => match get_reader(&entry) {
                Ok(reader) => {
                    let mut writer = get_writer(&entry, multiple_files);

                    if cli.count {
                        count_matches(reader, &mut writer, &pattern, cli.invert)?;
                    } else {
                        find_matches(reader, &mut writer, &pattern, cli.invert)?;
                    }
                }
                Err(e) => eprintln!("Failed to open {}: {e}", entry.display()),
            },
            Err(e) => eprintln!("{e}"),
        }
    }
    Ok(())
}
