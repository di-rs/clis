#![forbid(unsafe_code)]

mod cli;

use anyhow::{Context, Result};
use clap::CommandFactory;
use cli::Cli;
use std::{io::Write, process::ExitCode};

fn main() -> ExitCode {
    match Cli::try_parse_validated_from(std::env::args_os()) {
        Ok((cli, logging)) => cli_tracing::run::<Cli>(&logging, || match cli.command {
            Some(cli::Command::Build(args)) => cli::execute_build(&args),
            Some(cli::Command::Run(_) | cli::Command::Check(_)) => {
                anyhow::bail!("run/check execution is not implemented yet")
            }
            None => show_help(),
        }),
        Err(error) => {
            if error.print().is_err() {
                return ExitCode::FAILURE;
            }
            u8::try_from(error.exit_code()).map_or(ExitCode::FAILURE, ExitCode::from)
        }
    }
}

fn show_help() -> Result<ExitCode> {
    let mut output = std::io::stdout().lock();
    Cli::command()
        .write_help(&mut output)
        .context("Cannot write help")?;
    writeln!(output).context("Cannot finish help")?;
    output.flush().context("Cannot flush help")?;
    Ok(ExitCode::SUCCESS)
}
