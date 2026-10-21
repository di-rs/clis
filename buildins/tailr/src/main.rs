use std::{
    fs::File,
    io::{BufReader, BufWriter, Seek, Write},
    path::Path,
};

mod cli;
use crate::cli::{Cli, CliError};
use tailr::{get_total_lines, print_bytes, print_lines, print_stream_bytes, print_stream_lines};

fn main() -> std::process::ExitCode {
    cli_log::run("tailr", |cli| match run(&cli) {
        Ok(failed) => i32::from(failed),
        Err(CliError::FileOpen(_, e)) => {
            cli_log::diagnostic!("{e}");
            exitcode::CONFIG
        }
        Err(e) => {
            cli_log::diagnostic!("Error: {e}");
            exitcode::DATAERR
        }
    })
}

fn run(cli: &Cli) -> Result<bool, CliError> {
    let mut failed = false;
    let mut writer = get_writer();
    let num_files = cli.files.len();
    let stdin = std::io::stdin();
    let mut stdin = stdin.lock();

    for (file_num, filename) in cli.files.iter().enumerate() {
        if filename == Path::new("-") {
            if !cli.quiet && num_files > 1 {
                let delim = if file_num > 0 { "\n" } else { "" };
                writeln!(writer, "{delim}==> - <==")?;
            }
            if let Some(num_bytes) = cli.bytes {
                print_stream_bytes(&mut stdin, &mut writer, num_bytes)?;
            } else {
                print_stream_lines(&mut stdin, &mut writer, cli.lines)?;
            }
            continue;
        }
        let result = (|| -> Result<(), CliError> {
            let mut file = get_file_reader(filename)?;
            if !cli.quiet && num_files > 1 {
                let filename = filename.display();
                let delim = if file_num > 0 { "\n" } else { "" };
                writeln!(writer, "{delim}==> {filename} <==")?;
            }

            if let Some(num_bytes) = &cli.bytes {
                let metadata = file.metadata()?;
                let total_bytes = metadata.len();
                print_bytes(file, &mut writer, *num_bytes, total_bytes)?;
            } else {
                let total_lines = get_total_lines(&mut file)?;
                file.rewind()?;
                let reader = BufReader::new(file);
                print_lines(reader, &mut writer, cli.lines, total_lines)?;
            }
            Ok(())
        })();
        match result {
            Ok(()) => {}
            Err(e) => {
                failed = true;
                cli_log::diagnostic!("{}: {e}", filename.display());
            }
        }
    }
    writer.flush()?;
    Ok(failed)
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}

fn get_file_reader(path: &Path) -> Result<File, CliError> {
    let file = File::open(path).map_err(|e| CliError::FileOpen(path.to_owned(), e))?;
    Ok(file)
}
