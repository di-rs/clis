use anyhow::{Context, Result};
use clap::Parser;
use std::{
    io::{self, Write},
    process::ExitCode,
};

#[derive(Parser)]
#[command(name = "example")]
struct Cli {
    #[command(flatten)]
    logging: cli_tracing::LogArgs,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    cli_tracing::run::<Cli>(&cli.logging, work)
}

#[tracing::instrument(level = "debug", skip_all)]
fn work() -> Result<ExitCode> {
    log::info!("processing three items");
    let mut stdout = io::stdout().lock();
    writeln!(stdout, "3").context("Cannot write result")?;
    stdout.flush().context("Cannot flush result")?;
    Ok(ExitCode::SUCCESS)
}
