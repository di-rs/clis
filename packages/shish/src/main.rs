mod cli;
mod command;
mod parser;
mod prompt;

use std::io;
use std::process::ExitCode;

use clap::Parser;

/// Run commands in the shish shell.
#[derive(Parser)]
#[command(version, about)]
struct Args {
    /// Execute one command string instead of starting the prompt loop.
    #[arg(short = 'c')]
    command: Option<String>,
}

fn main() -> anyhow::Result<ExitCode> {
    let args = Args::parse();
    if let Some(command) = args.command {
        let status = cli::handle_user_input(&command)?;
        return Ok(ExitCode::from(u8::try_from(status).unwrap_or(1)));
    }
    loop {
        prompt::prompt()?;
        let command = get_user_input()?;
        if let Err(error) = cli::handle_user_input(&command) {
            eprintln!("{error}");
        }
    }
}

fn get_user_input() -> Result<String, io::Error> {
    let mut input = String::new();
    io::stdin().read_line(&mut input)?;
    Ok(input)
}
