mod runtime;
mod syntax;

use std::io::{self, IsTerminal as _, Read as _};
use std::path::Path;
use std::process::ExitCode;

use clap::Parser;

/// Run shell commands with Brush's sh/POSIX settings.
#[derive(Parser)]
#[command(version, about)]
#[expect(clippy::struct_excessive_bools, reason = "independent CLI switches")]
struct Args {
    /// Execute a command string; the first operand supplies $0.
    #[arg(short = 'c', conflicts_with = "stdin")]
    command: Option<String>,
    /// Force interactive shell behavior.
    #[arg(short = 'i')]
    interactive: bool,
    /// Read commands from stdin; operands supply positional parameters.
    #[arg(short = 's')]
    stdin: bool,
    /// Check syntax without executing commands.
    #[arg(short = 'n')]
    no_execute: bool,
    /// Select Brush's sh/POSIX settings (the default, not a conformance claim).
    #[arg(long = "posix")]
    _posix: bool,
    /// Script filename and arguments, or parameters for -c/-s.
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    operands: Vec<String>,
}

impl Args {
    fn script(&self) -> Option<&Path> {
        if self.command.is_some() || self.stdin {
            None
        } else {
            self.operands.first().map(Path::new)
        }
    }

    fn is_interactive(&self) -> bool {
        self.interactive
            || (self.command.is_none()
                && self.script().is_none()
                && io::stdin().is_terminal()
                && io::stderr().is_terminal())
    }

    fn syntax_input(&self) -> io::Result<String> {
        if let Some(command) = &self.command {
            Ok(command.clone())
        } else if let Some(script) = self.script() {
            std::fs::read_to_string(script)
        } else {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            Ok(input)
        }
    }
}

fn main() -> anyhow::Result<ExitCode> {
    let args = Args::parse();
    if args.no_execute {
        return match syntax::parse(&args.syntax_input()?) {
            Ok(_) => Ok(ExitCode::SUCCESS),
            Err(error) => {
                eprintln!("shish: {error}");
                Ok(ExitCode::from(2))
            }
        };
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(runtime::run(&args)).map(ExitCode::from)
}
