use anyhow::{Context, Result, bail};
use biggie::gen_random_lines;
use clap::Parser;
use cli_tracing::{Config, Destination, TracingSession};
use std::{
    fs::File,
    io::{self, BufWriter, Write},
    path::Path,
    process::ExitCode,
};
use thousands::Separable;

mod cli;
use crate::cli::Cli;

fn main() -> ExitCode {
    let cli = Cli::parse();
    match execute(&cli) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            let _ = writeln!(io::stderr().lock(), "biggie: {error:#}");
            ExitCode::FAILURE
        }
    }
}

fn execute(cli: &Cli) -> Result<()> {
    let config = Config::resolve(cli.diagnostic_overrides(), |key| std::env::var_os(key))?;
    let session = TracingSession::new(&config).context("Cannot open diagnostics")?;
    let operation =
        tracing::dispatcher::with_default(session.dispatch(), || run(cli, &config.destination));
    let diagnostics = session.finish().context("Cannot finish diagnostics");
    match (operation, diagnostics) {
        (Err(operation), Err(diagnostics)) => {
            return Err(operation.context(format!("Additionally: {diagnostics:#}")));
        }
        (Err(error), Ok(())) | (Ok(()), Err(error)) => return Err(error),
        (Ok(()), Ok(())) => {}
    }
    let mut stdout = io::stdout().lock();
    writeln!(
        stdout,
        r#"Done, wrote {} line{} to "{}"."#,
        cli.lines.separate_with_commas(),
        if cli.lines == 1 { "" } else { "s" },
        cli.file.display()
    )
    .context("Cannot write completion message")?;
    stdout.flush().context("Cannot flush completion message")
}

fn run(cli: &Cli, diagnostics: &Destination) -> Result<()> {
    let mut writer = get_writer(&cli.file, diagnostics)?;
    gen_random_lines(&mut writer, cli.lines)?;
    {
        let _span = tracing::info_span!(target: "clis::timing", "flush").entered();
        writer.flush().context("Cannot flush generated output")?;
    }
    tracing::info!(lines_written = cli.lines, "generated output");
    Ok(())
}

fn get_writer(path: &Path, diagnostics: &Destination) -> Result<BufWriter<File>> {
    // The diagnostic file is already created. Canonical paths also catch symlinks
    // and relative aliases before the data file can be truncated.
    if let Destination::File(log_path) = diagnostics
        && let Ok(data_path) = path.canonicalize()
        && data_path
            == log_path
                .canonicalize()
                .context("Cannot resolve diagnostic path")?
    {
        bail!("Data output and diagnostics refer to the same file");
    }
    let file =
        File::create(path).with_context(|| format!("Cannot create file {}", path.display()))?;
    Ok(BufWriter::new(file))
}
