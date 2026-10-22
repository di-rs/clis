//! CLI-to-Brush integration. Brush owns evaluation and shell state.

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufRead as _, IsTerminal as _};
use std::os::fd::AsFd as _;
use std::sync::Arc;

use brush_core::{ExecutionControlFlow, Shell, ShellVariable, SourceInfo};
use brush_interactive::{InteractiveOptions, InteractiveShell, ReedlineInputBackend, UIOptions};

use crate::Args;

pub async fn run(args: &Args) -> anyhow::Result<u8> {
    let shell_name = if args.stdin {
        "shish"
    } else {
        args.operands.first().map_or("shish", String::as_str)
    };
    let shell_args = if args.stdin {
        args.operands.clone()
    } else {
        args.operands.iter().skip(1).cloned().collect()
    };
    // Use an unbuffered descriptor for builtins too: Rust's shared stdin buffer
    // must not consume bytes intended for the next shell command.
    let stdin = File::from(io::stdin().as_fd().try_clone_to_owned()?);
    let mut shell = Shell::builder()
        .builtins(brush_builtins::default_builtins(
            brush_builtins::BuiltinSet::ShMode,
        ))
        .shell_name(shell_name.to_owned())
        .shell_args(shell_args)
        .shell_product_display_str(format!("shish {}", env!("CARGO_PKG_VERSION")))
        .interactive(args.is_interactive())
        .read_commands_from_stdin(args.command.is_none() && args.script().is_none())
        .command_string_mode(args.command.is_some())
        .sh_mode(true)
        .posix(true)
        .profile(brush_core::ProfileLoadBehavior::Skip)
        .rc(brush_core::RcLoadBehavior::Skip)
        .fds(HashMap::from([(0, stdin.into())]))
        .var(
            "PS1",
            ShellVariable::new(std::env::var("PS1").unwrap_or_else(|_| String::from(r"\W > "))),
        )
        .build()
        .await?;

    if let Some(command) = &args.command {
        return Ok(shell
            .run_dash_c_command(command.clone())
            .await?
            .exit_code
            .into());
    }
    if let Some(script) = args.script() {
        return Ok(shell
            .run_script(script, args.operands.iter().skip(1).cloned())
            .await?
            .exit_code
            .into());
    }
    if args.is_interactive() && io::stdin().is_terminal() {
        let shell = Arc::new(tokio::sync::Mutex::new(shell));
        let options = UIOptions::default();
        let mut input = ReedlineInputBackend::new(&options, &shell)?;
        InteractiveShell::new(&shell, &mut input, &InteractiveOptions::from(&options))?
            .run_interactively()
            .await?;
        return Ok(shell.lock().await.last_exit_status());
    }
    run_stdin(&mut shell).await
}

async fn run_stdin(shell: &mut Shell) -> anyhow::Result<u8> {
    let stdin = File::from(io::stdin().as_fd().try_clone_to_owned()?);
    // Capacity one prevents read-ahead from consuming command input (e.g. read).
    let mut reader = io::BufReader::with_capacity(1, stdin);
    let mut input = String::new();
    loop {
        let at_eof = reader.read_line(&mut input)? == 0;
        if at_eof && input.is_empty() {
            break;
        }
        let incomplete = match shell.parse_string(input.clone()) {
            Err(brush_parser::ParseError::ParsingAtEndOfInput) => true,
            Err(brush_parser::ParseError::Tokenizing { inner, .. }) => inner.is_incomplete(),
            _ => false,
        };
        if incomplete && !at_eof {
            continue;
        }
        let params = shell.default_exec_params();
        let result = shell
            .run_string(
                std::mem::take(&mut input),
                &SourceInfo::from("stdin"),
                &params,
            )
            .await?;
        if matches!(result.next_control_flow, ExecutionControlFlow::ExitShell) || at_eof {
            break;
        }
    }
    shell.on_exit().await?;
    Ok(shell.last_exit_status())
}
