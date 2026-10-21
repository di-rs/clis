use std::io::{BufWriter, Write};

mod cli;
use crate::cli::{Cli, CliError};
use lsr::{find_files, get_formatted_output};

fn main() -> std::process::ExitCode {
    cli_log::run("lsr", |cli| match run(&cli) {
        Ok(failed) => i32::from(failed),
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    })
}

fn run(cli: &Cli) -> Result<bool, CliError> {
    let mut writer = get_writer();

    let mut failed = false;
    let mut paths = Vec::new();
    for path in &cli.paths {
        match find_files(std::slice::from_ref(path), cli.show_hidden) {
            Ok(found) => paths.extend(found),
            Err(error) => {
                cli_log::diagnostic!("{}: {error}", path.display());
                failed = true;
            }
        }
    }
    if cli.long {
        let output = get_formatted_output(&paths)?;
        writeln!(writer, "{output}")?;
    } else {
        for path in paths {
            writeln!(writer, "{}", path.display())?;
        }
    }

    writer.flush()?;
    Ok(failed)
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
