use std::{io::BufWriter, io::Write};

use color_eyre::Result;

mod cli;
use cli::Cli;
use pwdr::{logical_path, physical_path};

fn main() -> Result<std::process::ExitCode> {
    color_eyre::install()?;
    cli_log::try_run("pwdr", |cli| run(&cli))
}

fn run(cli: &Cli) -> Result<()> {
    let mut writer = get_writer();

    let path = if cli.physical_path {
        physical_path()?
    } else {
        logical_path()?
    };

    writeln!(writer, "{}", path.display())?;

    writer.flush()?;
    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
