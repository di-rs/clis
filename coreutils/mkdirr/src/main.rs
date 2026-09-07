use clap::Parser;
use color_eyre::Result;

mod cli;
use cli::Cli;
use mkdirr::create_directory;

fn main() -> Result<()> {
    color_eyre::install()?;
    let cli = Cli::parse();
    run(&cli)
}

fn run(cli: &Cli) -> Result<()> {
    for dir in &cli.dirs {
        create_directory(dir, cli.parent)?;
    }
    Ok(())
}
