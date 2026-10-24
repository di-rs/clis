//! Narrow adapters around published Brush history APIs.

use std::collections::HashMap;

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
    context: ExecutionContext<'_, SE>,
    mut args: Vec<CommandArg>,
) -> BoxFuture<'_, Result<ExecutionResult, Error>> {
    Box::pin(async move {
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
