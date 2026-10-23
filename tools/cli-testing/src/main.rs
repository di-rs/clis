mod cli;

use clap::{Parser, error::ErrorKind};
use serde_json::json;
use std::{io::Write as _, process::ExitCode};

fn main() -> ExitCode {
    let arguments: Vec<_> = std::env::args_os().collect();
    let json_mode = arguments
        .iter()
        .take_while(|argument| *argument != "--")
        .any(|argument| argument == "--json");
    let args = match cli::Args::try_parse_from(arguments) {
        Ok(args) => args,
        Err(error) => {
            let informational = matches!(
                error.kind(),
                ErrorKind::DisplayHelp | ErrorKind::DisplayVersion
            );
            let mut report =
                cli::Report::new(json!({"text": error.to_string()}), error.to_string());
            if !informational {
                report.data = serde_json::Value::Null;
                report.fail(
                    "invalid_arguments",
                    "invalid command arguments; run cli-testing --help",
                );
            }
            return emit(&report, json_mode, if informational { 0 } else { 2 });
        }
    };
    let json_mode = args.json;
    let report = cli::execute(&args).unwrap_or_else(|error| {
        let mut report = cli::Report::new(serde_json::Value::Null, format!("{error:#}\n"));
        // Child output and TOML parse diagnostics can contain catalog values.
        // Keep those in explicit human diagnostics, not machine error envelopes.
        report.fail(
            "operation_failed",
            &format!(
                "{} failed; rerun without --json for diagnostics",
                args.command.name()
            ),
        );
        report
    });
    let code = u8::from(report.error.is_some());
    emit(&report, json_mode, code)
}

fn emit(report: &cli::Report, json_mode: bool, code: u8) -> ExitCode {
    let result = if json_mode {
        let envelope = json!({
            "schema": 1,
            "ok": code == 0,
            "data": report.data,
            "error": report.error,
        });
        writeln!(std::io::stdout().lock(), "{envelope}")
    } else if code == 0 {
        write!(std::io::stdout().lock(), "{}", report.text)
    } else {
        write!(std::io::stderr().lock(), "{}", report.text)
    };
    if result.is_err() {
        return ExitCode::FAILURE;
    }
    ExitCode::from(code)
}
