use clap::Parser;
use color_eyre::Result;

mod cli;
use cli::Cli;
use touchr::file_proceed;

fn main() -> Result<()> {
    color_eyre::install()?;
    let cli = Cli::parse();
    run(&cli)
}

fn run(cli: &Cli) -> Result<()> {
    for file in &cli.files {
        file_proceed(file, cli.timestamp, cli.skip_create)?;
    }
    Ok(())
}
