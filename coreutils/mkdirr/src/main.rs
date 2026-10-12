use color_eyre::Result;

mod cli;
use cli::Cli;
use mkdirr::create_directory;

fn main() -> Result<std::process::ExitCode> {
    color_eyre::install()?;
    let logging = cli_log::init("mkdirr");
    let cli = match logging.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return Ok(status),
    };
    match run(&cli) {
        Ok(()) => Ok(logging.finish(0)),
        Err(error) => {
            cli_log::record_error(format_args!("{error:#}"));
            let _ = logging.finish(1);
            Err(error)
        }
    }
}

fn run(cli: &Cli) -> Result<()> {
    for dir in &cli.dirs {
        create_directory(dir, cli.parent)?;
    }
    Ok(())
}
