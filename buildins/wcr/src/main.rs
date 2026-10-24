use clap::CommandFactory;
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
};

mod cli;
use crate::cli::{Cli, CliError};
use wcr::{FileInfo, get_file_info};

fn main() -> std::process::ExitCode {
    if let Some(status) = cli_schema::emit::<Cli>() {
        return status;
    }
    cli_log::run("wcr", |cli| match run(cli) {
        Ok(failed) => i32::from(failed),
        Err(CliError::Config) => {
            cli_log::record_error(CliError::Config);
            exitcode::CONFIG
        }
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    })
}

fn run(mut cli: Cli) -> Result<bool, CliError> {
    let mut failed = false;
    cli.normalize();
    let mut writer = get_writer();
    let mut total_info = FileInfo::default();

    for filename in &cli.files {
        match get_reader(filename) {
            Ok(reader) => {
                let info = get_file_info(reader)?;
                cli.write_info_line(&mut writer, &info, filename)?;
                total_info.add(&info);
            }
            Err(e) => {
                failed = true;
                cli_log::diagnostic!("{filename}: {e}");
            }
        }
    }

    if cli.files.len() > 1 {
        cli.write_info_line(&mut writer, &total_info, "total")?;
    }

    writer.flush()?;
    Ok(failed)
}

fn get_reader(path: &str) -> Result<Box<dyn BufRead>, CliError> {
    if path == "-" {
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
