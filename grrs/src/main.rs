use anyhow::{Context, Result};
use clap::{CommandFactory, Parser};
use human_panic::setup_panic;
use log::info;
use std::io::Read;
use std::io::{BufReader, IsTerminal};
use std::path::Path;

use crate::cli::Cli;

mod cli;

fn main() {
    setup_panic!();
    info!("starting up");

    let args = Cli::parse();
    env_logger::Builder::new()
        .filter_level(args.verbosity.into())
        .init();

    ctrlc::set_handler(move || {
        println!("received Ctrl+C!");
    })
    .expect("ctrlc is always can attach handler");

    let result = run_cli(args);

    match result {
        Ok(_) => {
            std::process::exit(exitcode::OK);
        }
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run_cli(args: Cli) -> Result<()> {
    let reader = get_reader(&args.path)?;

    let stdout = std::io::stdout();
    let writer = std::io::BufWriter::new(stdout.lock());

    grrs::find_matches(&args.pattern, reader, writer)
}

fn get_reader(path: &Path) -> Result<BufReader<Box<dyn Read>>> {
    let read: Box<dyn Read> = match path == Path::new("-") {
        true => {
            if std::io::stdin().is_terminal() {
                Cli::command().print_help().expect("always can print help");
                std::process::exit(exitcode::CONFIG);
            }
            Box::new(std::io::stdin().lock())
        }
        false => {
            let file = std::fs::File::open(path)
                .with_context(|| format!("could not read file '{}'", path.display()))?;
            Box::new(file)
        }
    };
    Ok(BufReader::new(read))
}
