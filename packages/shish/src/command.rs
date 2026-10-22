use anyhow::bail;
use std::env::current_dir;
use std::path::{Path, PathBuf};
use std::process;
use std::process::{Child, Stdio};
use std::str::FromStr;

pub fn execute_command(
    args: &[String],
    previous: Option<Child>,
    stdout: Stdio,
) -> anyhow::Result<Child> {
    let (command_name, args) = args
        .split_first()
        .ok_or_else(|| anyhow::anyhow!("missing command"))?;
    let stdin = match previous {
        Some(output) => Stdio::from(
            output
                .stdout
                .ok_or_else(|| anyhow::anyhow!("missing pipeline output"))?,
        ),
        None => Stdio::inherit(),
    };
    if let Some(command) = find_command_in_builins(command_name) {
        spawn_command(command, args, stdin, stdout)
    } else if let Some(command) = find_command_in_path(command_name) {
        spawn_command(command, args, stdin, stdout)
    } else if matches!(is_command_valid_path(command_name), Ok(true)) {
        spawn_command("cd", &[command_name], stdin, stdout)
    } else {
        bail!("{command_name}: command not found")
    }
}

fn find_command_in_builins(command: &str) -> Option<PathBuf> {
    let debug_buildin_path = PathBuf::from_str("target/debug/").ok()?;
    // let buildin_name = String::from("shish-") + command;
    let buildin = debug_buildin_path.join(command);
    match buildin.try_exists() {
        Ok(true) => Some(buildin),
        _ => None,
    }
}

fn find_command_in_path(command: &str) -> Option<PathBuf> {
    let path_env = std::env::var("PATH").ok()?;
    path_env.split(':').find_map(|dir| {
        let path = Path::new(dir).join(command);
        match path.try_exists() {
            Ok(true) => Some(path),
            _ => None,
        }
    })
}

fn is_command_valid_path(command: &str) -> Result<bool, std::io::Error> {
    let command = if command.eq("...") { "../.." } else { command };
    let current_dir = current_dir()?;
    let path = current_dir.join(command);
    path.try_exists()
}

fn spawn_command<A: AsRef<Path>>(
    command: A,
    args: &[impl AsRef<Path>],
    stdin: Stdio,
    stdout: Stdio,
) -> anyhow::Result<Child> {
    let command = command.as_ref();
    let output = process::Command::new(command)
        .args(args.iter().map(AsRef::as_ref))
        .stdin(stdin)
        .stdout(stdout)
        .spawn();
    match output {
        Ok(output) => Ok(output),
        Err(e) => {
            bail!("{}: {}", command.display(), e)
        }
    }
}
