use anyhow::{Context, Result, bail};
use clap::Parser;
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, IsTerminal, Write, stdin},
    process::ExitCode,
};
mod cli;
use cli::Cli;
use parsu::parse_xml;

fn main() -> ExitCode {
    if let Some(status) = cli_schema::emit::<Cli>() {
        return status;
    }
    let json = std::env::args_os()
        .skip(1)
        .take_while(|arg| arg != "--")
        .any(|arg| arg == "--json");
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error) => {
            let code = error.exit_code();
            if json && code != 0 {
                let _ = write_json(
                    &serde_json::json!({"ok": false, "error": {"kind": "arguments", "message": error.to_string()}}),
                );
            } else {
                let _ = error.print();
            }
            return if code == 0 {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(2)
            };
        }
    };
    let session = cli_log::init("parsu");
    let result = run(&cli);
    if let Err(error) = &result {
        cli_log::record_error(format_args!("{error:#}"));
        if cli.json {
            let _ = write_json(
                &serde_json::json!({"ok": false, "error": {"kind": "operation", "message": format!("{error:#}")}}),
            );
        } else {
            eprintln!("parsu: {error:#}");
        }
    }
    session.finish(i32::from(result.is_err()))
}

fn write_json(value: &serde_json::Value) -> Result<()> {
    let mut writer = BufWriter::new(std::io::stdout().lock());
    serde_json::to_writer(&mut writer, value)?;
    writeln!(writer)?;
    writer.flush()?;
    Ok(())
}

fn run(cli: &Cli) -> Result<()> {
    let mut reader = get_reader(&cli.file)?;
    let mut buf = String::new();
    reader
        .read_to_string(&mut buf)
        .context("cannot read UTF-8 input")?;
    let parsed = parse_xml(&buf).map_err(|rest| {
        let offset = buf.len().saturating_sub(rest.len());
        anyhow::anyhow!("cannot parse part of the input: unparsed input starts at byte {offset}")
    })?;
    if cli.json {
        write_json(&serde_json::json!({"ok": true, "data": parsed}))
    } else {
        let mut writer = BufWriter::new(std::io::stdout().lock());
        writeln!(writer, "{parsed:?}")?;
        writer.flush()?;
        Ok(())
    }
}

fn get_reader(path: &str) -> Result<Box<dyn BufRead>> {
    if path == "-" {
        if stdin().is_terminal() {
            bail!("`-` cannot be provided within tty");
        }
        Ok(Box::new(BufReader::new(stdin().lock())))
    } else {
        Ok(Box::new(BufReader::new(
            File::open(path).with_context(|| format!("cannot open file {path}"))?,
        )))
    }
}
