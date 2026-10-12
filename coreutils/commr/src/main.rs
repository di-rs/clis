use clap::CommandFactory;
use std::{
    fs::File,
    io::{BufRead, BufReader, IsTerminal, stdin},
    path::Path,
};

mod cli;
use crate::cli::{Cli, CliError};
use commr::get_lines;

fn main() -> std::process::ExitCode {
    let logging = cli_log::init("commr");
    let cli = match logging.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return status,
    };

    let status = match run(&cli) {
        Ok(()) => exitcode::OK,
        Err(CliError::Config) => {
            cli_log::record_error(CliError::Config);
            let _ = Cli::command().print_help();
            exitcode::CONFIG
        }
        Err(CliError::FileOpen(file_name, e)) => {
            cli_log::diagnostic!("{}: {e}", file_name.display());
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
    let file1 = &cli.file1;
    let file2 = &cli.file2;
    if file1 == Path::new("-") && file2 == Path::new("-") {
        return Err(CliError::BothFilesStdin);
    }

    let reader1 = get_reader(file1)?;
    let reader2 = get_reader(file2)?;

    get_lines(reader1, reader2, cli.insensitive, cli);

    Ok(())
}

fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            Err(CliError::Config)
        } else {
            Ok(Box::new(BufReader::new(stdin().lock())))
        }
    } else {
        let file = File::open(path).map_err(|e| CliError::FileOpen(path.to_owned(), e))?;
        Ok(Box::new(BufReader::new(file)))
    }
}
