//! Narrow adapters around published Brush history APIs.

use std::collections::HashMap;
use std::fs::File;
use std::io::Write as _;
use std::path::PathBuf;

use brush_core::builtins::{BoxFuture, Registration};
use brush_core::{CommandArg, Error, ExecutionContext, ExecutionResult, ShellExtensions};

pub fn builtins<SE: ShellExtensions>(
    set: brush_builtins::BuiltinSet,
) -> HashMap<String, Registration<SE>> {
    let mut registrations = brush_builtins::default_builtins(set);
    if let Some(history) = registrations.get_mut("history") {
        history.execute_func = execute;
    }
    registrations
}

fn execute<SE: ShellExtensions>(
    mut context: ExecutionContext<'_, SE>,
    mut args: Vec<CommandArg>,
) -> BoxFuture<'_, Result<ExecutionResult, Error>> {
    Box::pin(async move {
        if let Some(option) = invalid_option(&args) {
            writeln!(context.stderr(), "history: invalid option: {option}")?;
            return Ok(brush_core::ExecutionExitCode::InvalidUsage.into());
        }
        if let [_, CommandArg::String(option), rest @ ..] = args.as_slice()
            && option == "-r"
            && rest.len() <= 1
        {
            return read_history(&mut context, rest.first().map(ToString::to_string));
        }
        if let [_, CommandArg::String(option), rest @ ..] = args.as_slice()
            && matches!(option.as_str(), "-w" | "-a")
            && rest.len() <= 1
            && rest
                .first()
                .is_none_or(|arg| !arg.to_string().starts_with('-'))
        {
            return write_history(
                &mut context,
                rest.first().map(ToString::to_string),
                option == "-a",
            );
        }
        // Brush 0.2.0 subtracts the requested count without saturating. Clamp
        // only display operands; numeric filenames and delete offsets retain
        // their original meaning. Preserve Brush's formatting and diagnostics.
        let mut count = context
            .shell
            .history()
            .map_or(0, brush_core::history::History::count);
        for arg in args.iter_mut().skip(1) {
            let CommandArg::String(value) = arg else {
                break;
            };
            match value.as_str() {
                "-c" => count = 0,
                "--" => {}
                _ => {
                    if let Ok(requested) = value.parse::<usize>() {
                        *value = requested.min(count).to_string();
                    }
                    break;
                }
            }
        }
        let Some(original) =
            brush_builtins::default_builtins::<SE>(brush_builtins::BuiltinSet::BashMode)
                .remove("history")
        else {
            return Err(brush_core::ErrorKind::HistoryNotEnabled.into());
        };
        (original.execute_func)(context, args).await
    })
}

fn read_history<SE: ShellExtensions>(
    context: &mut ExecutionContext<'_, SE>,
    filename: Option<String>,
) -> Result<ExecutionResult, Error> {
    if context.shell.history().is_none() {
        return Err(brush_core::ErrorKind::HistoryNotEnabled.into());
    }
    let path = filename
        .map(PathBuf::from)
        .or_else(|| context.shell.history_file_path());
    let Some(path) = path else {
        return Ok(ExecutionResult::success());
    };
    let file = File::open(context.shell.working_dir().join(path))?;
    // Match Brush's startup import size policy. Parse before changing the
    // current history so an I/O failure cannot partially append entries.
    if file.metadata()?.len() > 1024 * 1024 * 1024 {
        return Err(brush_core::ErrorKind::HistoryFileTooLargeToImport.into());
    }
    let imported = brush_core::history::History::import(file)?;
    let history = context
        .shell
        .history_mut()
        .ok_or(brush_core::ErrorKind::HistoryNotEnabled)?;
    for item in imported.iter() {
        history.add(item.clone())?;
    }
    Ok(ExecutionResult::success())
}

fn write_history<SE: ShellExtensions>(
    context: &mut ExecutionContext<'_, SE>,
    filename: Option<String>,
    append: bool,
) -> Result<ExecutionResult, Error> {
    let path = filename
        .map(PathBuf::from)
        .or_else(|| context.shell.history_file_path());
    let path = path.map(|path| context.shell.working_dir().join(path));
    let timestamps = context.shell.history_time_format().is_some();
    let history = context
        .shell
        .history_mut()
        .ok_or(brush_core::ErrorKind::HistoryNotEnabled)?;
    if let Some(path) = path {
        history.flush(path, append, append, timestamps)?;
    }
    Ok(ExecutionResult::success())
}

// Brush treats unknown flags as positional arguments and may ignore them when
// a file operation is present. Validate option spelling without interpreting
// counts, file paths, offsets, or the literal arguments to -p/-s.
fn invalid_option(args: &[CommandArg]) -> Option<&str> {
    let mut args = args.iter().skip(1);
    while let Some(CommandArg::String(arg)) = args.next() {
        if !arg.starts_with('-') || matches!(arg.as_str(), "-" | "--") {
            break;
        }
        if arg == "--help" {
            continue;
        }
        let mut flags = arg.chars();
        flags.next();
        while let Some(flag) = flags.next() {
            match flag {
                'a' | 'n' | 'r' | 'w' | 'c' | 'h' => {}
                'd' => {
                    if flags.as_str().is_empty() {
                        args.next();
                    }
                    break;
                }
                'p' | 's' => return None,
                _ => return Some(arg),
            }
        }
    }
    None
}
