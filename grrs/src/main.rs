use anyhow::{Context, Result};
use clap::{CommandFactory, Parser};
use human_panic::setup_panic;
use log::info;
use std::io::Read;
use std::io::{BufReader, IsTerminal};
use std::path::Path;

use crate::cli::Cli;
use crate::clierror::CliError;

mod cli;
mod clierror;

fn main() {
    setup_panic!();
    info!("starting up");

    let args = Cli::parse();
    env_logger::Builder::new()
        .filter_level(args.verbosity.into())
        .init();

    let _ = ctrlc::set_handler(move || {
        println!("received Ctrl+C!");
    })
    .context("always can attach ctrl-c handler");

    let result = run_cli(&args);

    match result {
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

fn run_cli(args: &Cli) -> Result<(), CliError> {
    let reader = get_reader(&args.path)?;

    let stdout = std::io::stdout();
    let writer = std::io::BufWriter::new(stdout.lock());

    grrs::find_matches(&args.pattern, reader, writer)?;
    Ok(())
}

fn get_reader(path: &Path) -> Result<BufReader<Box<dyn Read>>, CliError> {
    let read: Box<dyn Read> = if path == Path::new("-") {
        if std::io::stdin().is_terminal() {
            let _ = Cli::command().print_help().context("always can print help");
            return Err(CliError::Config);
        }
        Box::new(std::io::stdin().lock())
    } else {
        let file = std::fs::File::open(path)
            .with_context(|| format!("could not read file '{}'", path.display()))?;
        Box::new(file)
    };
    Ok(BufReader::new(read))
}
