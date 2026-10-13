use clap::Parser;
use std::{
    io::{self, Write},
    process::ExitCode,
};

#[derive(Parser)]
struct Cli {}

fn main() -> ExitCode {
    cli_log::run("example", |_: Cli| {
        log::info!(items = 3; "processing items");
        let mut stdout = io::stdout().lock();
        match writeln!(stdout, "3").and_then(|()| stdout.flush()) {
            Ok(()) => 0,
            Err(error) => {
                cli_log::diagnostic!("example: cannot write result: {error}");
                1
            }
        }
    })
}
