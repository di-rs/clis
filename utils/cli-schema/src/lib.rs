#![doc = include_str!("../README.md")]

use std::{collections::BTreeMap, io::Write, process::ExitCode};

use clap::{Arg, ArgAction, Command, CommandFactory};
use serde_json::{Value, json};

fn requested() -> bool {
    std::env::var_os("SANCTUARY_CLI_SCHEMA").is_some_and(|value| value == "1")
        && std::env::args_os().skip(1).eq(["--help"])
}

/// Export the parser's public interface when the metadata invocation is requested.
/// Call before initializing logging or performing application work.
#[must_use]
pub fn emit<C: CommandFactory>() -> Option<ExitCode> {
    requested().then(|| print(&schema(C::command())))
}

/// Export an argument-insensitive utility, without adding help or version flags.
#[must_use]
pub fn emit_empty(name: &'static str, about: &'static str) -> Option<ExitCode> {
    requested().then(|| {
        print(&schema(
            Command::new(name)
                .about(about)
                .disable_help_flag(true)
                .disable_version_flag(true)
                .disable_help_subcommand(true),
        ))
    })
}

fn print(value: &Value) -> ExitCode {
    let mut stdout = std::io::stdout().lock();
    let result = serde_json::to_writer(&mut stdout, value)
        .map_err(std::io::Error::other)
        .and_then(|()| stdout.write_all(b"\n"))
        .and_then(|()| stdout.flush());
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("could not export CLI schema: {error}");
            ExitCode::FAILURE
        }
    }
}

/// Describe visible commands and arguments. Environment values are never exported.
#[must_use]
pub fn schema(mut command: Command) -> Value {
    command.build();
    json!({"schema_version": 1, "command": describe(&mut command, &[], &BTreeMap::new())})
}

fn describe(
    command: &mut Command,
    path: &[String],
    inherited: &BTreeMap<String, Vec<String>>,
) -> Value {
    let mut owners = inherited.clone();
    for arg in command.get_arguments().filter(|arg| arg.is_global_set()) {
        owners
            .entry(arg.get_id().to_string())
            .or_insert_with(|| path.to_vec());
    }
    let args: Vec<_> = command
        .get_arguments()
        .filter(|arg| {
            !(arg.is_hide_set() || arg.is_hide_short_help_set() && arg.is_hide_long_help_set())
        })
        .map(|arg| {
            argument(
                arg,
                if arg.is_global_set() {
                    owners
                        .get(arg.get_id().as_str())
                        .map_or(path, Vec::as_slice)
                } else {
                    path
                },
            )
        })
        .collect();
    let subcommands: Vec<_> = command
        .get_subcommands_mut()
        .filter(|child| !child.is_hide_set())
        .map(|child| {
            let mut child_path = path.to_vec();
            child_path.push(child.get_name().to_owned());
            describe(child, &child_path, &owners)
        })
        .collect();
    json!({
        "name": command.get_name(),
        "aliases": command.get_visible_aliases().collect::<Vec<_>>(),
        "about": command.get_about().map_or_default(ToString::to_string),
        "long_about": command.get_long_about().map(ToString::to_string),
        "usage": command.render_usage().to_string(),
        "usage_override": command.get_overridden_usage().map(ToString::to_string),
        "args": args,
        "subcommands": subcommands,
    })
}

fn argument(arg: &Arg, owner: &[String]) -> Value {
    let action = match arg.get_action() {
        ArgAction::Set => "set",
        ArgAction::Append => "append",
        ArgAction::SetTrue => "set_true",
        ArgAction::SetFalse => "set_false",
        ArgAction::Count => "count",
        ArgAction::Help | ArgAction::HelpShort | ArgAction::HelpLong => "help",
        ArgAction::Version => "version",
        _ => "unknown",
    };
    let range = arg.get_num_args().unwrap_or_default();
    let mut aliases = Vec::new();
    aliases.extend(
        arg.get_visible_aliases()
            .unwrap_or_default()
            .into_iter()
            .map(|alias| format!("--{alias}")),
    );
    aliases.extend(
        arg.get_visible_short_aliases()
            .unwrap_or_default()
            .into_iter()
            .map(|alias| format!("-{alias}")),
    );
    let defaults: Vec<_> = if arg.is_hide_default_value_set() {
        Vec::new()
    } else {
        arg.get_default_values()
            .iter()
            .map(|value| value.to_string_lossy().into_owned())
            .collect()
    };
    let values: Vec<_> = if arg.is_hide_possible_values_set() {
        Vec::new()
    } else {
        arg.get_possible_values()
            .into_iter()
            .filter(|value| !value.is_hide_set())
            .map(|value| value.get_name().to_owned())
            .collect()
    };
    json!({
        "id": arg.get_id().as_str(), "short": arg.get_short().map(|value| value.to_string()), "long": arg.get_long(),
        "aliases": aliases,
        "help": arg.get_help().map_or_default(ToString::to_string),
        "long_help": arg.get_long_help().map(ToString::to_string),
        "value_names": arg.get_value_names().map_or_default(|values| values.iter().map(ToString::to_string).collect::<Vec<_>>()),
        "default_values": defaults, "possible_values": values,
        "required": arg.is_required_set(), "index": arg.get_index(),
        "num_args": {"min": range.min_values(), "max": (range.max_values() != usize::MAX).then_some(range.max_values())},
        "action": action, "global": arg.is_global_set(), "owner": owner,
    })
}
