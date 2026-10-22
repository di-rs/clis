mod cli;
mod command;
mod parser;
mod prompt;
mod syntax;

use std::io::{self, IsTerminal as _, Read as _};
use std::process::ExitCode;

use clap::Parser;

/// Run commands in the shish shell.
#[derive(Parser)]
#[command(version, about)]
struct Args {
    /// Execute one command string instead of starting the prompt loop.
    #[arg(short = 'c')]
    command: Option<String>,
    /// Check syntax without executing commands.
    #[arg(short = 'n')]
    no_execute: bool,
}

fn main() -> anyhow::Result<ExitCode> {
    let args = Args::parse();
    if args.no_execute {
        let input = if let Some(command) = args.command {
            command
        } else {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            input
        };
        return match syntax::parse(&input) {
            Ok(_) => Ok(ExitCode::SUCCESS),
            Err(error) => {
                eprintln!("shish: {error}");
                Ok(ExitCode::from(2))
            }
        };
    }
    if let Some(command) = args.command {
        let status = cli::handle_user_input(&command)?;
        return Ok(ExitCode::from(u8::try_from(status).unwrap_or(1)));
    }
    let interactive = io::stdin().is_terminal();
    let mut status = 0;
    loop {
        if interactive {
            prompt::prompt()?;
        }
        let Some(command) = get_user_input()? else {
            return Ok(ExitCode::from(u8::try_from(status).unwrap_or(1)));
        };
        match cli::handle_user_input(&command) {
            Ok(code) => status = code,
            Err(error) => {
                eprintln!("{error}");
                status = 1;
            }
        }
    }
}

fn get_user_input() -> Result<Option<String>, io::Error> {
    let mut input = String::new();
    if io::stdin().read_line(&mut input)? == 0 {
        return Ok(None);
    }
    Ok(Some(input))
}
