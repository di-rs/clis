use clap::Parser;
use std::{
    io::{self, Write},
    process::ExitCode,
};

#[derive(Parser)]
struct Cli {}

fn main() -> ExitCode {
    let session = cli_log::init("example");
    if let Err(status) = session.parse::<Cli>() {
        return status;
    }
    log::info!(items = 3; "processing items");
    let mut stdout = io::stdout().lock();
    match writeln!(stdout, "3").and_then(|()| stdout.flush()) {
        Ok(()) => session.finish(0),
        Err(error) => {
            cli_log::diagnostic!("example: cannot write result: {error}");
            session.finish(1)
        }
    }
}
