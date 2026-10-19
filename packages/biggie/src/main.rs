use anyhow::{Context, Result};
use biggie::{generate_bytes, generate_fields, generate_records, generate_text};
use std::process::ExitCode;
mod cli;
mod output;
use cli::{Cli, Command, ParsedCli};
fn main() -> ExitCode {
    let cli = match Cli::parse_validated() {
        Ok(cli) => cli,
        Err(error) => {
            let session = if matches!(
                error.kind(),
                clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
            ) {
                cli_log::init_with_level("biggie", log::LevelFilter::Off)
            } else if let Some(level) = Cli::error_log_level() {
                cli_log::init_with_level("biggie", level)
            } else {
                cli_log::init("biggie")
            };
            return session.clap_error(&error);
        }
    };
    let session = cli_log::init_with_level("biggie", cli.logging.level());
    let status = match execute(&cli) {
        Ok(()) => 0,
        Err(error) => {
            cli_log::diagnostic!("biggie: {error:#}");
            1
        }
    };
    session.finish(status)
}
fn execute(cli: &ParsedCli) -> Result<()> {
    match &cli.command {
        Command::Records(args) => {
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
        Command::Fields(args) => {
            let options = args.options();
            output::single(&args.file, options.lines, "record", |writer| {
                generate_fields(writer, &options)
            })?;
        }
        Command::Bytes(args) => {
            let options = args.options();
            output::single(&args.file, options.bytes, "byte", |writer| {
                generate_bytes(writer, &options)
            })?;
        }
        Command::Text(args) => write_text(args)?,
        Command::Pair(args) => output::pair(&args.left, &args.right, &args.options())?,
    }
    Ok(())
}

fn write_text(args: &cli::text::TextArgs) -> Result<()> {
    let options = args.options();
    output::single(&args.file, options.generation.lines, "line", |writer| {
        generate_text(writer, &options)
    })
}
