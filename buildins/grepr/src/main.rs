use clap::CommandFactory;
use grepr::{count_matches, find_files, find_matches};
use human_panic::setup_panic;
use log::info;
use std::io::Write;

mod cli;
use crate::cli::{Cli, CliError, get_reader, get_writer};

fn main() -> std::process::ExitCode {
    if let Some(status) = cli_schema::emit::<Cli>() {
        return status;
    }
    setup_panic!();
    let logging = cli_log::init("grepr");
    let cli = match logging.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return status,
    };
    logging.set_stderr_level(cli.verbosity.log_level_filter());
    info!("starting up");

    let status = match run(&cli) {
        Ok(failed) => i32::from(failed),
        Err(CliError::Config) => {
            cli_log::record_error(CliError::Config);
            let _ = Cli::command().print_help();
            exitcode::CONFIG
        }
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    };
    logging.finish(status)
}

fn run(cli: &Cli) -> Result<bool, CliError> {
    let mut failed = false;
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
                    writer.flush()?;
                }
                Err(e) => {
                    failed = true;
                    cli_log::diagnostic!("Failed to open {}: {e}", entry.display());
                }
            },
            Err(e) => {
                failed = true;
                cli_log::diagnostic!("{e}");
            }
        }
    }
    Ok(failed)
}
