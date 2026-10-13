use color_eyre::Result;

mod cli;
use cli::Cli;
use touchr::file_proceed;

fn main() -> Result<std::process::ExitCode> {
    color_eyre::install()?;
    cli_log::try_run("touchr", |cli| run(&cli))
}

fn run(cli: &Cli) -> Result<()> {
    for file in &cli.files {
        file_proceed(file, cli.timestamp, cli.skip_create)?;
    }
    Ok(())
}
