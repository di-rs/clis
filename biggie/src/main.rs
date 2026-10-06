use anyhow::{Context, Result};
use biggie::{generate_bytes, generate_fields, generate_records, generate_text};
use std::process::ExitCode;
mod cli;
mod output;
use cli::{Cli, Command};
fn main() -> ExitCode {
    let cli = Cli::parse_validated();
    cli_tracing::run::<Cli>(&cli.logging, || execute(&cli))
}
fn execute(cli: &Cli) -> Result<ExitCode> {
    match &cli.command {
        Some(Command::Records(args)) => {
            let options = args.options().context("Cannot load record corpus")?;
            if args.file.as_os_str() != "-"
                && let Some(input) = &args.records_file
            {
                output::reject_alias(input, &args.file)?;
            }
            output::single(&args.file, options.total_records()?, "record", |writer| {
                generate_records(writer, &options)
            })?;
        }
        Some(Command::Fields(args)) => {
            let options = args.options();
            output::single(&args.file, options.lines, "record", |writer| {
                generate_fields(writer, &options)
            })?;
        }
        Some(Command::Bytes(args)) => {
            let options = args.options();
            output::single(&args.file, options.bytes, "byte", |writer| {
                generate_bytes(writer, &options)
            })?;
        }
        Some(Command::Text(args)) => write_text(args)?,
        None => write_text(&cli.text)?,
        Some(Command::Pair(args)) => output::pair(&args.left, &args.right, &args.options())?,
    }
    Ok(ExitCode::SUCCESS)
}

fn write_text(args: &cli::text::TextArgs) -> Result<()> {
    let options = args.options();
    output::single(&args.file, options.generation.lines, "line", |writer| {
        generate_text(writer, &options)
    })
}
