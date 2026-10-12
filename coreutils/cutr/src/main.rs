use clap::CommandFactory;
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
    path::Path,
};

mod cli;
use crate::cli::{Cli, CliError};
use cutr::Extract;

fn main() -> std::process::ExitCode {
    let logging = cli_log::init("cutr");
    let cli = match logging.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return status,
    };

    let status = match run(&cli) {
        Ok(()) => exitcode::OK,
        Err(CliError::Config) => {
            cli_log::record_error(CliError::Config);
            exitcode::CONFIG
        }
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    };
    logging.finish(status)
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let mut writer = get_writer();

    for filename in &cli.files {
        match get_reader(filename) {
            Ok(reader) => {
                let extractor = Extract::try_from(&cli.extract)?;
                extractor.extract(reader, &mut writer, cli.delimiter)?;
            }
            Err(e) => cli_log::diagnostic!("Failed to open {}: {e}", filename.display()),
        }
    }
    writer.flush()?;
    Ok(())
}

fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            let _ = Cli::command().print_help();
            return Err(CliError::Config);
        }
        Ok(Box::new(BufReader::new(stdin().lock())))
    } else {
        Ok(Box::new(BufReader::new(File::open(path)?)))
    }
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
