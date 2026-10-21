use anyhow::Result;

mod cli;
use cli::Cli;
use mkdirr::create_directory;

fn main() -> Result<std::process::ExitCode> {
    cli_log::try_run("mkdirr", |cli| run(&cli))
}

fn run(cli: &Cli) -> Result<()> {
    for dir in &cli.dirs {
        create_directory(dir, cli.parent)?;
    }
    Ok(())
}
