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

    let args = parser::args(input);

    let mut command_args = vec![];
    let mut previous_command = None;

    for arg in args {
        match arg.as_str() {
            "|" => {
                let _stdout = get_stdout(&mut command_args)?;
                let c = execute_command(&command_args, previous_command, Stdio::piped())?;
                previous_command = Some(c);
                command_args.clear();
            }
            "&&" | "||" => {
                let stdout = get_stdout(&mut command_args)?.unwrap_or_else(Stdio::inherit);
                let mut c = execute_command(&command_args, previous_command, stdout)?;

                if arg == "&&" {
                    let res = c.wait()?;
                    if !res.success() {
                        return Ok(res.code().unwrap_or(1));
                    }
                } else if arg == "||" && c.wait()?.success() {
                    return Ok(0);
                }
                previous_command = None;
                command_args.clear();
            }
            _ => command_args.push(arg),
        }
    }

    if command_args.is_empty() {
        return Ok(0);
    }
    {
        let stdout = get_stdout(&mut command_args)?.unwrap_or_else(Stdio::inherit);
        let mut c = execute_command(&command_args, previous_command, stdout)?;
        let res = c.wait()?;
        Ok(res.code().unwrap_or(1))
    }
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
