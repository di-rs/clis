use clap::Parser;
use std::io::Write;

#[derive(Debug, Parser)]
#[command(author, version, about)]
/// Rust version of `echo`
struct Args {
    /// Input text
    #[arg(required(true))]
    text: Vec<String>,

    /// Do not print newline
    #[arg(short('n'))]
    omit_newline: bool,
}

fn main() -> std::process::ExitCode {
    let logging = cli_log::init("echor");
    let Args { text, omit_newline } = match logging.parse::<Args>() {
        Ok(args) => args,
        Err(status) => return status,
    };
    let ending = if omit_newline { "" } else { "\n" };
    let mut stdout = std::io::stdout().lock();
    let result = write!(stdout, "{}{ending}", text.join(" ")).and_then(|()| stdout.flush());
    let status = match result {
        Ok(()) => 0,
        Err(error) => {
            cli_log::diagnostic!("Error: {error}");
            1
        }
    };
    logging.finish(status)
}
