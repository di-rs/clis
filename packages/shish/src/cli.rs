use anyhow::bail;
use std::fs;
use std::process::Stdio;

use crate::command::execute_command;
use crate::parser;

pub fn handle_user_input(input: &str) -> anyhow::Result<i32> {
    let input = input.trim();

    if input.is_empty() {
        return Ok(0);
    }

    let mut pipeline = Vec::new();
    let mut status = 0;
    let mut should_run = true;
    for arg in parser::args(input) {
        if matches!(arg.as_str(), "&&" | "||") {
            if should_run {
                status = execute_pipeline(&pipeline)?;
            }
            should_run = if arg == "&&" {
                status == 0
            } else {
                status != 0
            };
            pipeline.clear();
        } else {
            pipeline.push(arg);
        }
    }
    if should_run && !pipeline.is_empty() {
        status = execute_pipeline(&pipeline)?;
    }
    Ok(status)
}

fn execute_pipeline(args: &[String]) -> anyhow::Result<i32> {
    let mut commands = args.split(|arg| arg == "|").peekable();
    let mut children: Vec<std::process::Child> = Vec::new();
    while let Some(args) = commands.next() {
        let mut args = args.to_vec();
        let stdout = get_stdout(&mut args)?.unwrap_or_else(|| {
            if commands.peek().is_some() {
                Stdio::piped()
            } else {
                Stdio::inherit()
            }
        });
        let stdin = children
            .last_mut()
            .and_then(|child| child.stdout.take())
            .map_or_else(Stdio::inherit, Stdio::from);
        match execute_command(&args, stdin, stdout) {
            Ok(child) => children.push(child),
            Err(error) => {
                for child in &mut children {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                return Err(error);
            }
        }
    }
    let mut status = 0;
    for mut child in children {
        status = child.wait()?.code().unwrap_or(1);
    }
    Ok(status)
}

fn get_stdout(args: &mut Vec<String>) -> anyhow::Result<Option<Stdio>> {
    if !is_valid_redirection(args) {
        return Ok(None);
    }

    let file_name = args
        .pop()
        .ok_or_else(|| anyhow::anyhow!("missing redirection filename"))?;
    args.pop(); // Remove the ">" symbol

    match fs::File::open(&file_name).or_else(|_| fs::File::create(&file_name)) {
        Ok(file) => Ok(Some(Stdio::from(file))),
        Err(e) => bail!("{e}"),
    }
}

fn is_valid_redirection(args: &[String]) -> bool {
    matches!(args, [.., command, redirect, _] if !command.is_empty() && redirect == ">")
}
