use anyhow::{Context, Result};
use biggie::gen_random_lines;
use clap::Parser;
use std::{
    fs::File,
    io::{self, BufWriter, Write},
    process::ExitCode,
};
use thousands::Separable;

mod cli;
use crate::cli::Cli;

fn main() -> ExitCode {
    let cli = Cli::parse();
    cli_tracing::run::<Cli>(&cli.logging, || execute(&cli))
}

fn execute(cli: &Cli) -> Result<ExitCode> {
    let file = File::create(&cli.file)
        .with_context(|| format!("Cannot create file {}", cli.file.display()))?;
    let mut writer = BufWriter::new(file);
    gen_random_lines(&mut writer, cli.lines)?;
    {
        let _span = tracing::debug_span!("flush").entered();
        writer.flush().context("Cannot flush generated output")?;
    }
    log::info!("generated output: {} lines", cli.lines);
    let mut stdout = io::stdout().lock();
    writeln!(
        stdout,
        r#"Done, wrote {} line{} to "{}"."#,
        cli.lines.separate_with_commas(),
        if cli.lines == 1 { "" } else { "s" },
        cli.file.display(),
    )
    .context("Cannot write completion message")?;
    stdout.flush().context("Cannot flush completion message")?;
    Ok(ExitCode::SUCCESS)
}
