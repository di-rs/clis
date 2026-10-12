use std::{io::BufWriter, io::Write};

use color_eyre::Result;

mod cli;
use cli::Cli;
use pwdr::{logical_path, physical_path};

fn main() -> Result<std::process::ExitCode> {
    color_eyre::install()?;
    let logging = cli_log::init("pwdr");
    let cli = match logging.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return Ok(status),
    };
    match run(&cli) {
        Ok(()) => Ok(logging.finish(0)),
        Err(error) => {
            cli_log::record_error(format_args!("{error:#}"));
            let _ = logging.finish(1);
            Err(error)
        }
    }
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
