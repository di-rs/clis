use anyhow::{Context, Result};
use biggie::{generate_bytes, generate_fields, generate_records, generate_text};
use std::process::ExitCode;
mod cli;
mod output;
use cli::{Cli, Command, ParsedCli};
fn main() -> ExitCode {
    if let Some(status) = cli_schema::emit::<Cli>() {
        return status;
    }
    let cli = match Cli::parse_validated() {
        Ok(cli) => cli,
        Err(error) => {
            let session = if matches!(
                error.kind(),
                clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
            ) {
                cli_log::init_with_level("biggie", log::LevelFilter::Off)
            } else if let Some(level) = Cli::error_log_level() {
                cli_log::init_with_level("biggie", level)
            } else {
                cli_log::init("biggie")
            };
            if error.exit_code() != 0
                && std::env::args_os()
                    .skip(1)
                    .take_while(|a| a != "--")
                    .any(|a| a == "--json")
            {
                let _ = emit_json(
                    &serde_json::json!({"ok": false, "error": {"kind": "arguments", "message": error.to_string()}}),
                );
                return session.finish(2);
            }
            return session.clap_error(&error);
        }
    };
    let session = cli_log::init_with_level("biggie", cli.logging.level());
    let status = match execute(&cli) {
        Ok(()) => 0,
        Err(error) => {
            if cli.json {
                cli_log::record_error(format_args!("{error:#}"));
                let _ = emit_json(
                    &serde_json::json!({"ok": false, "error": {"kind": "operation", "message": format!("{error:#}")}}),
                );
            } else {
                cli_log::diagnostic!("biggie: {error:#}");
            }
            1
        }
    };
    session.finish(status)
}
fn execute(cli: &ParsedCli) -> Result<()> {
    let record_options = match &cli.command {
        Command::Records(args) => Some(args.options().context("Cannot load record corpus")?),
        _ => None,
    };
    let plan = if cli.json || cli.dry_run {
        generation_plan(cli, record_options.as_ref())?
    } else {
        serde_json::Value::Null
    };
    if cli.dry_run {
        if cli.json {
            emit_json(&serde_json::json!({"ok": true, "data": plan}))?;
        } else {
            emit_json(&plan)?;
        }
        return Ok(());
    }
    match &cli.command {
        Command::Records(args) => {
            let options = record_options.as_ref().context("Missing record options")?;
            if args.file.as_os_str() != "-"
                && let Some(input) = &args.records_file
            {
                output::reject_alias(input, &args.file)?;
            }
            output::single(
                &args.file,
                options.total_records()?,
                "record",
                !cli.json,
                |writer| generate_records(writer, options),
            )?;
        }
        Command::Fields(args) => {
            let options = args.options();
            output::single(&args.file, options.lines, "record", !cli.json, |writer| {
                generate_fields(writer, &options)
            })?;
        }
        Command::Bytes(args) => {
            let options = args.options();
            output::single(&args.file, options.bytes, "byte", !cli.json, |writer| {
                generate_bytes(writer, &options)
            })?;
        }
        Command::Text(args) => write_text(args, !cli.json)?,
        Command::Pair(args) => output::pair(&args.left, &args.right, &args.options(), !cli.json)?,
    }
    if cli.json {
        emit_json(&serde_json::json!({"ok": true, "data": plan}))?;
    }
    Ok(())
}

fn write_text(args: &cli::text::TextArgs, report: bool) -> Result<()> {
    let options = args.options();
    output::single(
        &args.file,
        options.generation.lines,
        "line",
        report,
        |writer| generate_text(writer, &options),
    )
}

fn emit_json(value: &serde_json::Value) -> Result<()> {
    use std::io::Write;
    let mut stdout = std::io::stdout().lock();
    serde_json::to_writer(&mut stdout, value)?;
    writeln!(stdout)?;
    stdout.flush()?;
    Ok(())
}

fn generation_plan(
    cli: &ParsedCli,
    records: Option<&biggie::RecordOptions>,
) -> Result<serde_json::Value> {
    use serde_json::json;
    let (operation, outputs) = match &cli.command {
        Command::Text(a) => (
            "text",
            vec![
                json!({"path": a.file.to_string_lossy(), "count": a.options().generation.lines, "unit": "records"}),
            ],
        ),
        Command::Fields(a) => (
            "fields",
            vec![
                json!({"path": a.file.to_string_lossy(), "count": a.options().lines, "unit": "records"}),
            ],
        ),
        Command::Bytes(a) => (
            "bytes",
            vec![
                json!({"path": a.file.to_string_lossy(), "count": a.options().bytes, "unit": "bytes"}),
            ],
        ),
        Command::Records(a) => {
            let options = records.context("Missing record options")?;
            if a.file.as_os_str() != "-"
                && let Some(input) = &a.records_file
            {
                output::reject_alias(input, &a.file)?;
            }
            (
                "records",
                vec![
                    json!({"path": a.file.to_string_lossy(), "count": options.total_records()?, "unit": "records"}),
                ],
            )
        }
        Command::Pair(a) => {
            output::reject_alias(&a.left, &a.right)?;
            let (left, right) = a.options().record_counts()?;
            (
                "pair",
                vec![
                    json!({"path": a.left.to_string_lossy(), "count": left, "unit": "records"}),
                    json!({"path": a.right.to_string_lossy(), "count": right, "unit": "records"}),
                ],
            )
        }
    };
    anyhow::ensure!(
        !cli.json || cli.dry_run || outputs.iter().all(|o| o.get("path") != Some(&json!("-"))),
        "--json requires named outputs or --dry-run; stdout is reserved for generated data"
    );
    Ok(json!({"operation": operation, "dry_run": cli.dry_run, "outputs": outputs}))
}
