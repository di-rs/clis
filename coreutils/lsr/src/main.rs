use std::io::{BufWriter, Write};

mod cli;
use crate::cli::{Cli, CliError};
use lsr::{find_files, get_formatted_output};

fn main() -> std::process::ExitCode {
    let logging = cli_log::init("lsr");
    let cli = match logging.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return status,
    };

    let status = match run(&cli) {
        Ok(()) => exitcode::OK,
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    };
    logging.finish(status)
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let mut writer = get_writer();

    let paths = find_files(&cli.paths, cli.show_hidden)?;
    if cli.long {
        let output = get_formatted_output(&paths)?;
        writeln!(writer, "{output}")?;
    } else {
        for path in paths {
            writeln!(writer, "{}", path.display())?;
        }
    }

    writer.flush()?;
    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
