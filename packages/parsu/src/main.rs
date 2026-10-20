use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
};

use anyhow::{Context, Result, bail};
use clap::CommandFactory;

mod cli;
use cli::Cli;

use parsu::parse_xml;

fn main() -> Result<std::process::ExitCode> {
    cli_log::try_run("parsu", |cli| run(&cli))
}

fn run(cli: &Cli) -> Result<()> {
    let mut writer = get_writer();
    let mut reader = get_reader(&cli.file)?;

    let mut buf = String::new();
    reader.read_to_string(&mut buf)?;

    match parse_xml(&buf) {
        Ok(parsed_doc) => writeln!(writer, "{parsed_doc:?}")?,
        Err(e) => bail!("cannot parse part of the input: {e}"),
    }

    writer.flush()?;
    Ok(())
}

fn get_reader(path: &str) -> Result<Box<dyn BufRead>> {
    match path {
        "-" => {
            if stdin().is_terminal() {
                let _ = Cli::command().print_help();
                bail!("`-` cannot be provided within tty");
            }
            Ok(Box::new(BufReader::new(stdin().lock())))
        }
        path => {
            let file = File::open(path).with_context(|| format!("cannot open file {path}"))?;
            Ok(Box::new(BufReader::new(file)))
        }
    }
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
